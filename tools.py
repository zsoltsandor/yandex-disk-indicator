#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from gi import require_version
require_version('GLib', '2.0')
from gi.repository.GLib import timeout_add, source_remove
require_version('Gtk', '3.0')
from gi.repository import Gtk
from subprocess import call


class Timer(object):            # Timer for triggering a function periodically
  ''' Timer class methods:
        __init__ - initialize the timer object with specified interval and handler. Start it
                   if start value is not False. par - is parameter for handler call.
        start    - Start timer. Optionally the new interval can be specified and if timer is
                   already running then the interval is updated (timer restarted with new interval).
        update   - Updates interval. If timer is running it is restarted with new interval. If it
                   is not running - then new interval is just stored.
        stop     - Stop running timer or do nothing if it is not running.
      Interface variables:
        active   - True when timer is currently running, otherwise - False
  '''
  def __init__(self, interval, handler, par=None, start=True):
    self.interval = interval          # Timer interval (ms)
    self.handler = handler            # Handler function
    self.par = par                    # Parameter of handler function
    self.active = False               # Current activity status
    if start:
      self.start()                    # Start timer if required

  def start(self, interval=None):   # Start inactive timer or update if it is active
    if interval is None:
      interval = self.interval
    if not self.active:
      self.interval = interval
      if self.par is None:
        self.timer = timeout_add(interval, self.handler)
      else:
        self.timer = timeout_add(interval, self.handler, self.par)
      self.active = True
      # logger.debug('timer started %s %s' %(self.timer, interval))
    else:
      self.update(interval)

  def update(self, interval):         # Update interval (restart active, not start if inactive)
    if interval != self.interval:
      self.interval = interval
      if self.active:
        self.stop()
        self.start()

  def stop(self):                     # Stop active timer
    if self.active:
      # logger.debug('timer to stop %s' %(self.timer))
      source_remove(self.timer)
      self.active = False

