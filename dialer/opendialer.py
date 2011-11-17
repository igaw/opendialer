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
import os
import subprocess
import resources
import logging
from PyQt4 import QtGui, QtCore, uic

#------------------------------------------------------------------------------
# Local helper functions
#------------------------------------------------------------------------------
def show_syntax_and_exit():
    print "Syntax:"
    print "  %s -h, --help" % sys.argv[0]
    print "  %s [-d, --debug] [<modem-path>]" % sys.argv[0]
    sys.exit(0)

def try_async_dbus_call(object_path, interface_suffix, method_name,
                        expect_return_value, *args):
    try:
        interface = dbus.Interface(
            dbus.SystemBus().get_object("org.ofono", object_path),
            "org.ofono." + interface_suffix)
        method = getattr(interface, method_name)
        reply_func = None
        if expect_return_value:
            reply_func = lambda x: None
        else:
            reply_func = lambda: None
        method(*args, reply_handler=reply_func, error_handler=lambda e: None)
    except dbus.exceptions.DBusException:
        pass # Omit silently

#------------------------------------------------------------------------------
# VoiceCall
#------------------------------------------------------------------------------
class VoiceCall:
    def __init__(self, voicecall_path, voicecall_properties):
        # Init members
        self.voicecall_path = voicecall_path
        self.properties = voicecall_properties
        self.assigned_display = None # Setter is CallDisplay.assign_voicecall

#------------------------------------------------------------------------------
# VoiceCallDisplay
#------------------------------------------------------------------------------
class VoiceCallDisplay:
    def __init__(self, main_window, modem_path, display, state_label,
                 button_green, button_orange, button_red):
        self.main_window = main_window
        self.modem_path = modem_path
        self.voicecall = None
        self.display = display
        self.state_label = state_label
        self.button_green = button_green
        self.button_orange = button_orange
        self.button_red = button_red
        self.buttons = [ button_green, button_orange, button_red ]
        self.button_callbacks = [ None, None, None ]
        self.button_geometries = [
            button_red.geometry(),
            button_orange.geometry(),
            button_green.geometry()]
        for button_id in range(3):
            self.connect_button(button_id)
        self.build_palette_dict()

    def connect_button(self, button_id):
        self.main_window.connect(
            self.buttons[button_id], QtCore.SIGNAL("clicked()"),
            lambda: self.on_button_clicked(button_id))
                                          
    def on_button_clicked(self, button_id):
        if self.button_callbacks[button_id] != None:
            self.button_callbacks[button_id]()

    def build_palette_dict(self):
        state_color_dict = dict()
        state_color_dict["disconnected"] = QtGui.QColor(0, 0, 0)
        state_color_dict["active"] = QtGui.QColor(75, 200, 75) # green
        state_color_dict["held"] = QtGui.QColor(210, 130, 75) # orange
        state_color_dict["waiting"] = QtGui.QColor(255, 0, 255) # light pink
        state_color_dict["incoming"] = QtGui.QColor(255, 0, 255) # light pink
        state_color_dict["alerting"] = QtGui.QColor(220, 220, 220) # light grey
        state_color_dict["dialing"] = QtGui.QColor(220, 220, 220) # light grey
        self.palette_dict = dict()
        for (state, color) in state_color_dict.items():
            new_palette = self.display.palette()
            new_palette.setColor(QtGui.QPalette.Text, color)
            new_palette.setColor(QtGui.QPalette.WindowText, color)
            self.palette_dict[state] = new_palette

    def assign_voicecall(self, voicecall):
        if self.voicecall != None:
            if self.voicecall.assigned_display == self: # Assigned to two?
                self.voicecall.assigned_display = None
        self.voicecall = voicecall
        if self.voicecall != None:
            # Last one assigned to wins
            self.voicecall.assigned_display = self

    def get_voicecall_state(self):
        voicecall_state = "disconnected"
        if self.voicecall != None:
            if self.voicecall.properties.has_key("State"): # Just in case
                voicecall_state = self.voicecall.properties["State"]
        return voicecall_state

    def is_multiparty(self):
        if self.voicecall == None:
            return False
        else:
            return bool(self.voicecall.properties["Multiparty"])

    def update_widget_state(self, other_voicecall_state):
        green_callback = None
        orange_callback = None
        red_callback = None
        green_tooltip = ""
        orange_tooltip = ""
        red_tooltip = ""

        # Set text
        display_text = ""
        if self.voicecall != None:
            if self.is_multiparty():
                display_text = "multiparty"
            elif self.voicecall.properties.has_key("LineIdentification"):
                display_text = self.voicecall.properties["LineIdentification"]

        # Check voicecall state
        voicecall_state = self.get_voicecall_state()

        # -- Active calls --
        if voicecall_state == "active":
            # Orange: hold or swap --> Disabled if incoming (or waiting) exists
            if other_voicecall_state == "disconnected":
                orange_callback = self.do_swap_calls
                orange_tooltip = "Put call on hold"
            elif other_voicecall_state == "held":
                orange_callback = self.do_swap_calls
                orange_tooltip = "Swap held call"

            # Red: different behavior depending whether waiting call exists
            if other_voicecall_state == "waiting":
                # Red (and waiting call exists): relase active and answer
                red_callback = self.do_release_and_answer
                red_tooltip = "Hang-up call and answer waiting"
            else:
                # Red (and no waiting call): simple hang-up
                red_callback = self.do_hangup
                if not self.is_multiparty():
                    red_tooltip = "Hang-up this call"
                else:
                    red_tooltip = "Hang-up multiparty call"

        # -- Held calls --
        elif voicecall_state == "held":
            # Green: unhold --> enabled only if no other call exists
            if other_voicecall_state == "disconnected":
                green_callback = self.do_swap_calls
                green_tooltip = "Unhold call"

        # -- Alerting (or dialing) calls --
        elif voicecall_state in [ "dialing", "alerting" ]:
            # Red: hang-up
            red_callback = self.do_hangup
            red_tooltip = "Cancel"

        # -- Incoming calls --
        elif voicecall_state == "incoming":
            # Green: accept
            green_callback = self.do_answer
            green_tooltip = "Answer call"
            # Red: reject
            red_callback = self.do_hangup
            red_tooltip = "Reject call"

        # -- Waiting calls --
        elif voicecall_state == "waiting":
            # Green: put active on hold and accept waiting call
            if other_voicecall_state == "active":
                green_callback = self.do_hold_and_answer
                green_tooltip = "Hold active call and aswer"
            else:
                green_callback = self.do_release_and_answer
                green_tooltip = "Answer waiting call"
            # Red: reject:
            red_callback = self.do_hangup
            red_tooltip = "Reject call"

        # Perform the actual changes
        self.display.setPalette(self.palette_dict[voicecall_state])
        self.button_callbacks = [
            green_callback, orange_callback, red_callback ]
        button_tooltips = [ green_tooltip, orange_tooltip, red_tooltip ]
        
        visible_button_num = 0
        for i in reversed(range(3)):
            button_visible = (self.button_callbacks[i] != None)
            self.buttons[i].setVisible(button_visible)
            self.buttons[i].setGeometry(
                self.button_geometries[visible_button_num])
            self.buttons[i].setToolTip(button_tooltips[i])
            visible_button_num += button_visible
        self.display.clear()
        self.display.insertPlainText(display_text)
        if voicecall_state == "disconnected":
            self.state_label.setText("")
        else:
            self.state_label.setText(voicecall_state)

    def do_swap_calls(self):
        try_async_dbus_call(
            self.modem_path, "VoiceCallManager", "SwapCalls", False)

    def do_release_and_answer(self):
        try_async_dbus_call(
            self.modem_path, "VoiceCallManager", "ReleaseAndAnswer", False)

    def do_hangup(self):
        if self.voicecall != None:
            if self.is_multiparty():
                try_async_dbus_call(
                    self.modem_path, "VoiceCallManager", "HangupMultiparty",
                    False)
            else:
                try_async_dbus_call(
                    self.voicecall.voicecall_path, "VoiceCall", "Hangup", False)

    def do_answer(self):
        if self.voicecall != None:
            try_async_dbus_call(
                self.voicecall.voicecall_path, "VoiceCall", "Answer", False)

    def do_hold_and_answer(self):
        try_async_dbus_call(
            self.modem_path, "VoiceCallManager", "HoldAndAnswer", False)

#------------------------------------------------------------------------------
# PhoneDialog
#------------------------------------------------------------------------------
class PhoneDialog(QtGui.QMainWindow):

    pbap_gui_path = "../pbap-gui/"

    def __init__(self, modem_path):
        self.modem_path = modem_path
        self.pending_dial = None
        self.displays = [] # Necessary for first reconnect()
        self.init_gui()
        self.reconnect()
        self.install_signal_receivers()
        self.load_phonebook()
        self.update_widget_state()

    def init_gui(self):
        QtGui.QMainWindow.__init__(self)
        self.ui = uic.loadUi('dialer.ui')
        self.green_palette = self.ui.buttonDial.palette()
        self.red_palette = self.ui.buttonHangupAll.palette()
        self.button_palette = self.ui.buttonNumber1.palette()
        self.default_palette = self.ui.palette()
        self.displays = [ None, None ]
        self.displays[0] = VoiceCallDisplay(
            self, self.modem_path,
            self.ui.callDisplay0,
            self.ui.callDisplay0_state,
            self.ui.callDisplay0_green,
            self.ui.callDisplay0_orange,
            self.ui.callDisplay0_red)
        self.displays[1] = VoiceCallDisplay(
            self, self.modem_path,
            self.ui.callDisplay1,
            self.ui.callDisplay1_state,
            self.ui.callDisplay1_green,
            self.ui.callDisplay1_orange,
            self.ui.callDisplay1_red)
        self.ui.show()
        self._button_dict = dict()
        self._button_dict["0"] = self.ui.buttonNumber0
        self._button_dict["1"] = self.ui.buttonNumber1
        self._button_dict["2"] = self.ui.buttonNumber2
        self._button_dict["3"] = self.ui.buttonNumber3
        self._button_dict["4"] = self.ui.buttonNumber4
        self._button_dict["5"] = self.ui.buttonNumber5
        self._button_dict["6"] = self.ui.buttonNumber6
        self._button_dict["7"] = self.ui.buttonNumber7
        self._button_dict["8"] = self.ui.buttonNumber8
        self._button_dict["9"] = self.ui.buttonNumber9
        self._button_dict["*"] = self.ui.buttonStar
        self._button_dict["#"] = self.ui.buttonHash

    def reconnect(self):
        self.modem_powered = False
        self.call_dict = dict()
        for display in self.displays:
            display.assign_voicecall(None)
        modem_interface = dbus.Interface(
            dbus.SystemBus().get_object("org.ofono", self.modem_path),
            "org.ofono.Modem")
        modem_properties = modem_interface.GetProperties()
        self.modem_powered = bool(modem_properties["Powered"])
        self.ui.setWindowTitle(modem_properties["Name"])
        self.device_address = modem_properties.get("Serial")
        try:
            voicecallmanager_interface = dbus.Interface(
                dbus.SystemBus().get_object("org.ofono", self.modem_path),
                "org.ofono.VoiceCallManager")
            for (path, properties) in voicecallmanager_interface.GetCalls():
                self.register_call(path, properties)
        except dbus.exceptions.DBusException:
            pass # Modem probably not powered

    def install_signal_receivers(self):
        bus = dbus.SystemBus()
        bus.add_signal_receiver(
            self.signal_ofono_name_owner_changed,
            dbus_interface="org.freedesktop.DBus",
            signal_name="NameOwnerChanged")
        bus.add_signal_receiver(
            self.signal_modem_property_changed,
            dbus_interface="org.ofono.Modem",
            signal_name="PropertyChanged",
            path_keyword="modem_path")
        bus.add_signal_receiver(
            self.signal_call_added,
            dbus_interface="org.ofono.VoiceCallManager",
            signal_name="CallAdded")
        bus.add_signal_receiver(
            self.signal_call_removed,
            dbus_interface="org.ofono.VoiceCallManager",
            signal_name="CallRemoved")
        bus.add_signal_receiver(
            self.signal_voicecall_property_changed,
            dbus_interface="org.ofono.VoiceCall",
            signal_name="PropertyChanged",
            path_keyword="call_path")

        self.ui.installEventFilter(self)
        for (char, button) in self._button_dict.items():
            self.connect_button(char, button)
        self.connect(self.ui.buttonHangupAll, QtCore.SIGNAL("clicked()"),
                     lambda: self.hangup_all_clicked())
        self.connect(self.ui.buttonMultiparty, QtCore.SIGNAL("clicked()"),
                     lambda: self.multiparty_clicked())
        self.connect(self.ui.buttonDial, QtCore.SIGNAL("clicked()"),
                     lambda: self.dial_clicked())
        self.connect(self.ui.buttonPower, QtCore.SIGNAL("clicked()"),
                     lambda: self.power_clicked())
        self.connect(self.ui.buttonPbap, QtCore.SIGNAL("clicked()"),
                     lambda: self.pbap_clicked())
        self.connect(self.ui.dialerComboBox,
                     QtCore.SIGNAL("activated(QString)"),
                     self.dialer_item_activated)

    def load_phonebook(self):
        try:
            f = open("phonebook.txt")
            numbers = f.readlines()
            f.close()
            self.ui.dialerComboBox.setVisible(False)
            for number in numbers:
                self.ui.dialerComboBox.addItem(number.strip())
            self.ui.dialerComboBox.clearEditText()
        except:
            pass # Omit silently
        self.ui.dialerComboBox.setVisible(True)

    def connect_button(self, char, button):
        self.connect(button, QtCore.SIGNAL("clicked()"),
                     lambda: self.number_clicked(char))

    def number_clicked(self, char):
        char_key = 0
        if char == "*":
            char_key = QtCore.Qt.Key_Asterisk
        elif char == "#":
            char_key = QtCore.Qt.Key_NumberSign
        else:
            char_key = int(char)
        self.ui.dialerComboBox.keyPressEvent(
            QtGui.QKeyEvent(QtCore.QEvent.KeyPress,
                            char_key,
                            QtCore.Qt.KeyboardModifiers(),
                            str(char)))
        self.ui.dialerComboBox.setFocus()

    def power_clicked(self):
        try_async_dbus_call(
            self.modem_path, "Modem", "SetProperty", False,
            "Powered", dbus.Boolean(1))

    def pbap_clicked(self):
        if not self.device_address:
            return
        owd = os.getcwd()
        try:
            os.chdir(self.pbap_gui_path)
            subprocess.Popen(["/usr/bin/python", "pbap-gui.py",
                              self.device_address])
        finally:
            os.chdir(owd)

    def hangup_all_clicked(self):
        try_async_dbus_call(
            self.modem_path, "VoiceCallManager", "HangupAll", False)

    def multiparty_clicked(self):
        try_async_dbus_call(
            self.modem_path, "VoiceCallManager", "CreateMultiparty", True)

    def dial_clicked(self):
        self.ui.dialerComboBox.keyPressEvent(
            QtGui.QKeyEvent(QtCore.QEvent.KeyPress,
                            QtCore.Qt.Key_Enter,
                            QtCore.Qt.KeyboardModifiers()))

    def dialer_item_activated(self, number_string):
        # Clear the combo box
        self.ui.dialerComboBox.clearEditText()
        # Perform the call
        # FIXME: this is probably racy
        if self.get_current_state_string() == "active":
            self.pending_dial = str(number_string)
            try_async_dbus_call(
                self.modem_path, "VoiceCallManager", "SwapCalls", False)
        else:
            self.pending_dial = None
            try_async_dbus_call(
                self.modem_path, "VoiceCallManager", "Dial", True,
                str(number_string), "")

    def eventFilter(self,  obj,  event):
        if event.type() == QtCore.QEvent.KeyPress:
            if event.key() == QtCore.Qt.Key_Escape:
                return False
            elif event.key() == QtCore.Qt.Key_Backspace:
                self.backspace_pressed()
                return True
            else:
                try:
                    text = str(event.text())
                    if self._button_dict.has_key(text):
                        self._button_dict[text].click()
                    return True
                except:
                    pass # Some chars just fail here
        return False

    def backspace_pressed(self):
        self.ui.dialerComboBox.textCursor().deletePreviousChar()

    def get_modem_path_from_call_path(self, call_path):
        index = call_path.rindex("/")
        modem_path = call_path[:index]
        return modem_path

    def signal_ofono_name_owner_changed(self, name, old_owner, new_owner):
        if name == "org.ofono":
            if new_owner != "":
                self.reconnect()
                self.update_widget_state()

    def signal_modem_property_changed(
        self, property_name, property_value, modem_path):
        if modem_path != self.modem_path:
            return
        if property_name == "Powered":
            self.modem_powered = bool(property_value)
            if not self.modem_powered:
                self.call_dict = dict()
            self.update_widget_state()

    def signal_call_added(self, call_path, properties):
        modem_path = self.get_modem_path_from_call_path(call_path)
        if modem_path != self.modem_path:
            return
        logging.debug("Call added: %s" % call_path)
        self.register_call(call_path, properties)

    def register_call(self, call_path, properties):
        self.pending_dial = None
        new_call = VoiceCall(call_path, properties)
        self.call_dict[call_path] = new_call
        for display in self.displays:
            if display.voicecall == None:
                display.assign_voicecall(new_call)
                break
        if new_call.properties.has_key("LineIdentification"):
            number = new_call.properties["LineIdentification"]
            logging.debug("Registering call with number %s" % number)
            if self.ui.dialerComboBox.findText(number) < 0:
                old_text = self.ui.dialerComboBox.currentText()
                self.ui.dialerComboBox.addItem(number)
                self.ui.dialerComboBox.setEditText(old_text)
        self.update_widget_state()

    def check_unassigned_calls(self):
        # Find a free display
        first_free_display = None
        for display in self.displays:
            if display.voicecall == None:
                first_free_display = display
                break
        if first_free_display == None:
            return # Nothing to do anyway

        # Check unassigned calls
        for call in self.call_dict.values():
            if call.assigned_display == None:
                if not bool(call.properties["Multiparty"]):
                    # For non-multiparty calls, just assign any free display
                    first_free_display.assign_voicecall(call)
                else:
                    # For multiparty, at least one call should be displayed
                    multiparty_displays = filter(
                        lambda x: x.is_multiparty(),
                        self.displays)
                    any_multiparty_shown = (len(multiparty_displays) > 0)
                    if not(any_multiparty_shown):
                        first_free_display.assign_voicecall(call)

    def signal_call_removed(self, call_path):
        modem_path = self.get_modem_path_from_call_path(call_path)
        if modem_path != self.modem_path:
            return
        logging.debug("Call removed: %s" % call_path)
        if self.call_dict.has_key(call_path):
            removed_call = self.call_dict[call_path]
            display = removed_call.assigned_display
            del self.call_dict[call_path]
            if display != None:
                # Release display
                display.assign_voicecall(None)
                # Check (just in case) if there is any known call
                # without a assigned display
                self.check_unassigned_calls()
        self.update_widget_state()

    def signal_voicecall_property_changed(
        self, property_name, property_value, call_path):
        modem_path = self.get_modem_path_from_call_path(call_path)
        if modem_path != self.modem_path:
            return
        if not self.call_dict.has_key(call_path):
            return
        old_state = self.get_current_state_string()
        voicecall = self.call_dict[call_path]
        voicecall.properties[property_name] = property_value
        new_state = self.get_current_state_string()
        if property_name == "State":
            logging.debug("Voicecall state changed to '%s'" % new_state)
            if self.pending_dial != None:
                if new_state == "held":
                    # Dial pending number
                    try_async_dbus_call(
                        self.modem_path, "VoiceCallManager", "Dial", True,
                        str(self.pending_dial), "")
                self.pending_dial = None
            # See if there is a held call to be activated
            if ((property_value == "disconnected") and
                (len(self.call_dict) == 2)):
                remaining_call = filter(
                    lambda c: (c != voicecall),
                    self.call_dict.values())[0]
                if remaining_call.properties["State"] == "held":
                    try_async_dbus_call(
                        self.modem_path, "VoiceCallManager", "SwapCalls", False)
            self.update_widget_state()

        elif (property_name == "Multiparty"):
            self.pending_dial = None
            if bool(property_value):
                # This call became part of a multiparty call
                # One single display should be used for multiparty, so check it
                multiparty_displays = filter(
                    lambda x: x.is_multiparty(),
                    self.displays)
                if ((len(multiparty_displays) > 1) and
                    (voicecall.assigned_display != None)):
                    # Unassign if necessary
                    if multiparty_displays[0].voicecall != voicecall:
                        voicecall.assigned_display.assign_voicecall(None)
            self.check_unassigned_calls()
            self.update_widget_state()

    def get_current_state_string(self):
        state_set = set()
        for call in self.call_dict.values():
            call_state = call.properties["State"]
            if call_state != "disconnected":
                state_set.add(call_state)
        if len(state_set) == 0:
            return "disconnected"
        elif len(state_set) == 1:
            return state_set.pop()
        else:
            return "(several-calls)"

    def update_widget_state(self):
        call_state = self.get_current_state_string()

        # Set the state of common widgets
        dialing_enabled = (
            self.modem_powered and
            (call_state in [ "disconnected", "held", "active" ]))
        self.ui.dialerComboBox.setEnabled(dialing_enabled)

        # Keypad
        for (char, button) in self._button_dict.items():
            button.setEnabled(dialing_enabled)
            if dialing_enabled:
                button.setPalette(self.button_palette)
            else:
                button.setPalette(self.default_palette)

        # Powering functionality
        self.ui.buttonPower.setEnabled(not self.modem_powered)
        self.ui.buttonPower.setVisible(not self.modem_powered)
        if not self.modem_powered:
            self.ui.statusLabel.setText("not-powered")
            for display in self.displays:
                display.assign_voicecall(None)
        else:
            self.ui.statusLabel.setText(call_state)

        # HangupAll button
        self.ui.buttonHangupAll.setEnabled(
            self.modem_powered and (len(self.call_dict) > 0) and
            call_state != "held")
        if self.ui.buttonHangupAll.isEnabled():
            self.ui.buttonHangupAll.setPalette(self.red_palette)
        else:
            self.ui.buttonHangupAll.setPalette(self.default_palette)

        # Dialing button
        self.ui.buttonDial.setEnabled(dialing_enabled)
        if self.ui.buttonDial.isEnabled():
            self.ui.buttonDial.setPalette(self.green_palette)
        else:
            self.ui.buttonDial.setPalette(self.default_palette)

        # Update displays
        create_multiparty_enabled = False
        for display_num in range(len(self.displays)):
            display = self.displays[display_num]
            other_display = self.displays[len(self.displays) - 1 - display_num]

            voicecall_state = display.get_voicecall_state()
            other_voicecall_state = other_display.get_voicecall_state()
            display.update_widget_state(other_voicecall_state)
            create_multiparty_enabled = (
                create_multiparty_enabled or
                ((voicecall_state == "active") and
                 (other_voicecall_state == "held")))

        # Multiparty button
        self.ui.buttonMultiparty.setEnabled(create_multiparty_enabled)

        # PBAP button
        self.ui.buttonPbap.setEnabled(
            (self.device_address != None) and (call_state == "disconnected"))

#------------------------------------------------------------------------------
# Main
#------------------------------------------------------------------------------
if __name__ == "__main__":
	dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
	app = QtGui.QApplication(sys.argv)

        # Parse arguments
        modem_path = None
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
            modem_path = list(nonflags)[0]

        # Initialize logging system
        logging.basicConfig(
            format = "[%(asctime)s] %(message)s",
            level = logging_level)

        # Take default modem
        if modem_path == None:
            manager = dbus.Interface(
                dbus.SystemBus().get_object("org.ofono", "/"),
                "org.ofono.Manager")
            modems = manager.GetModems()
            if len(modems) == 0:
                logging.error("ERROR: No modems available")
                sys.exit(1)
            modem_path = modems[0][0]

	win = PhoneDialog(modem_path)
	sys.exit(app.exec_())
