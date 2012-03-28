#!/usr/bin/env python

from distutils.core import setup

setup(name='opendialer',
      version='0.1',
      description='Open Source Dialer GUI',
      author='Mikel Astiz',
      author_email='mikel.astiz@bmw-carit.de',
      url='git://git.bmw-carit.de/opendialer.git',
      packages=['opendialer'],
      package_dir={'opendialer': 'dialer'},
      package_data={'opendialer': ['res/*.png', '*.ui']},
      data_files=[('share/applications', ['opendialer.desktop'])],
      license='GPLv2',
      options={'bdist_rpm': {'requires': 'PyQt4',
                             'group':    'User Interface/Desktops',
                             'vendor':   'The OpenDialer Team'}},
      scripts=['opendialer', 'utils/loopback-loader', 'utils/service-connector']
     )
