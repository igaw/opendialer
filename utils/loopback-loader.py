#!/usr/bin/python
#
#  OpenDialer - Open Source Dialer GUI
#
#  Copyright (C) 2011  BMW Car IT GmbH. All rights reserved.
#
#  This program is free software; you can redistribute it and/or modify
#  it under the terms of the GNU General Public License version 2 as
#  published by the Free Software Foundation.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software
#  Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA
#
import sys
import dbus
import dbus.mainloop.glib
import gobject
import string
import logging

#------------------------------------------------------------------------------
# Helper functions
#------------------------------------------------------------------------------
def show_syntax_and_exit():
    print "Syntax:"
    print "  %s -h, --help" % sys.argv[0]
    print "  %s [-d, --debug] [<bt-address>]" % sys.argv[0]
    sys.exit(0)

#------------------------------------------------------------------------------
# LoopbackLoader
#------------------------------------------------------------------------------
class LoopbackLoader:
    pulseaudio_dbus_name = "org.PulseAudio1"
    unwanted_modules = [ "module-suspend-on-idle" ]
    enabled_protocols = [ "hsp", "sco", "a2dp_source" ]

    def __init__(self, device_address):
        self.device_address = device_address # Can be None
        self.invalidate_connection()
        self.install_general_signal_receivers()
        try:
            self.reconnect()
        except dbus.exceptions.DBusException:
            pass # PulseAudio might not be running

    def install_general_signal_receivers(self):
        bus = dbus.SessionBus()
        bus.add_signal_receiver(
            self.name_owner_changed,
            dbus_interface="org.freedesktop.DBus",
            signal_name="NameOwnerChanged")

    def name_owner_changed(self, name, old_owner, new_owner):
        if name == self.pulseaudio_dbus_name:
            if new_owner in [ None, "" ]:
                self.invalidate_connection()
            else:
                self.reconnect()

    def reconnect(self):
        self.setup_connection()
        self.install_specific_signal_receivers()
        self.poll_initial_state()

    def setup_connection(self):
        bus = dbus.SessionBus()
        # Find out server address
        server_lookup = bus.get_object(
            self.pulseaudio_dbus_name,
            "/org/pulseaudio/server_lookup1")
        address = server_lookup.Get(
            "org.PulseAudio.ServerLookup1",
            "Address",
            dbus_interface="org.freedesktop.DBus.Properties")
        # Get core interface
        connection =  dbus.connection.Connection(address)
        self.pa_core = dbus.Interface(
            connection.get_object(
                object_path="/org/pulseaudio/core1"), "org.PulseAudio.Core1")
        self.pa_connection = connection

    def invalidate_connection(self):
        self.pa_connection = None
        self.pa_core = None
        self.fallback_source = None
        self.fallback_sink = None

    def install_specific_signal_receivers(self):
        self.pa_core.ListenForSignal(
            "org.PulseAudio.Core1.NewSink",
            [self.pa_core.proxy_object])
        self.pa_core.connect_to_signal("NewSink",self.new_sink)
        self.pa_core.ListenForSignal(
            "org.PulseAudio.Core1.NewSource",
            [self.pa_core.proxy_object])
        self.pa_core.connect_to_signal("NewSource",self.new_source)

    def poll_initial_state(self):
        prop_interface = dbus.Interface(
            self.pa_core.proxy_object,
            "org.freedesktop.DBus.Properties")
        # Some local functions to process the responses
        def sink_response(sinks):
            for sink in sinks:
                self.new_sink(sink)
        def source_response(sources):
            for source in sources:
                self.new_source(source)
        def fallback_sink_response(path):
            self.get_device_property(path, "Name", set_fallback_sink)
        def fallback_source_response(path):
            self.get_device_property(path, "Name", set_fallback_source)
        def set_fallback_sink(path):
            self.fallback_sink = path
        def set_fallback_source(path):
            self.fallback_source = path
        # List of properties we are interested in
        prop_requests = [
            ("Sinks", sink_response),
            ("Sources", source_response),
            ("FallbackSink", fallback_sink_response),
            ("FallbackSource", fallback_source_response)
            ]
        for (prop_name, prop_reply_handler) in prop_requests:
            try:
                prop_interface.Get(
                    "org.PulseAudio.Core1", prop_name,
                    reply_handler=prop_reply_handler,
                    error_handler=lambda e: None)
            except dbus.exceptions.DBusException:
                pass # Omit error silently

    def get_device_property(self, device_path, property_name, reply_handler):
        try:
            device_properties_interface = dbus.Interface(
                self.pa_connection.get_object(object_path=device_path),
                "org.freedesktop.DBus.Properties")
            device_properties_interface.Get(
                "org.PulseAudio.Core1.Device", property_name,
                reply_handler=reply_handler,
                error_handler=lambda e: reply_handler(None))
        except dbus.exceptions.DBusException:
            reply_handler(None)

    def get_from_property_list(self, device_path, property_name, reply_handler):
        # Local function to forward the property
        def forward_response(prop_list):
            if prop_list == None:
                reply_handler(None)
                return
            if not(prop_list.has_key(property_name)):
                reply_handler(None)
                return
            prop = bytearray(prop_list[property_name]).decode("utf-8")
            filtered = filter(lambda x: x in string.printable, prop)
            reply_handler(filtered)
        # Request the property list
        self.get_device_property(
            device_path, "PropertyList",
            reply_handler=forward_response)

    def get_common_sinksource_properties(self, path, reply_handler):
        # Some local functions to process the responses
        def name_response(name):
            self.get_from_property_list(
                path, "bluetooth.protocol",
                lambda p: protocol_response(name, p))
        def protocol_response(name, protocol):
            if protocol in self.enabled_protocols:
                self.get_from_property_list(
                    path, "device.string",
                    lambda dev_str:
                        device_string_response(name, protocol, dev_str))
        def device_string_response(name, protocol, dev_str):
            if self.device_address in [ None, dev_str ]:
                reply_handler(name, protocol, dev_str)
        # Start requesting the name
        self.get_device_property(path, "Name", name_response)

    def new_sink(self, sink_path):
        def properties_response(name, protocol, device_string):
            logging.debug("New sink: %s; protocol: %s" % (name, protocol))
            if self.fallback_source != None:
                self.load_loopback_module(self.fallback_source, name)
        self.get_common_sinksource_properties(sink_path, properties_response)

    def new_source(self, source_path):
        def properties_response(name, protocol, device_string):
            logging.debug("New source: %s; protocol: %s" % (name, protocol))
            if self.fallback_sink != None:
                self.load_loopback_module(name, self.fallback_sink)
        self.get_common_sinksource_properties(source_path, properties_response)

    def load_loopback_module(self, source_name, sink_name):
        logging.debug(
            "Loading module-loopback with source='%s' sink='%s'" % (
                source_name, sink_name))
        args = dict()
        args["source"] = source_name
        args["sink"] = sink_name
        args["source_dont_move"] = "1"
        args["sink_dont_move"] = "1"
        self.pa_core.LoadModule(
            "module-loopback", args,
            reply_handler=lambda p: None,
            error_handler=lambda e: None)

#------------------------------------------------------------------------------
# Main
#------------------------------------------------------------------------------
if __name__ == "__main__":
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)

        # Parse arguments
        device_address = None
        logging_level = logging.INFO
        flags = set(filter(lambda x: x.startswith("-"), sys.argv[1:]))
        nonflags = set(filter(lambda x: not(x.startswith("-")), sys.argv[1:]))
        if "-h" in flags or "--help" in flags:
            show_syntax_and_exit()
        if "-d" in flags or "--debug" in flags:
            flags.discard("-d")
            flags.discard("-debug")
            logging_level = logging.DEBUG
        if len(nonflags) > 1 or len(flags) > 0:
            show_syntax_and_exit()
        if len(nonflags) == 1:
            device_address = list(nonflags)[0]

        # Initialize logging system
        logging.basicConfig(
            format = "[%(asctime)s] %(message)s",
            level = logging_level)

        # Run main loop
        loopback_loader = LoopbackLoader(device_address)
        try:
            mainloop = gobject.MainLoop()
            mainloop.run()
        except KeyboardInterrupt:
            print
            print "Exiting"
