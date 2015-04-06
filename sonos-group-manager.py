#!/usr/bin/env python

#
#  sonos-group-manager.py
#
#  This script provides a server which monitors the topology changes of a Sonos network, and
#  will revert to a default state if all zones are paused.
#
#  Configure a default topology:
#
#      With all zones in the Sonos network paused, use your preferred controller to configure
#      the groups.  The new configuration will automatically be detected, and set as the default
#      topology.
#
#  Configure a temporary topology:
#
#      With at least one zone playing, use your preferred controller to configure the groups.  The
#      new configuration will automatically be detected as a temporary topology.  The script will
#      revert to the default topology once all zones have stopped playing.
#
#      A temporary topology is automatically created when a Playbar switches to TV input.
#

#
#  The MIT License (MIT)
#  
#  Copyright (c) 2015 Nnamdi Onyeyiri
#  
#  Permission is hereby granted, free of charge, to any person obtaining a copy of this software 
#  and associated documentation files (the "Software"), to deal in the Software without 
#  restriction, including without limitation the rights to use, copy, modify, merge, publish, 
#  distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the 
#  Software is furnished to do so, subject to the following conditions:
#  
#  The above copyright notice and this permission notice shall be included in all copies or 
#  substantial portions of the Software.
#  
#  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING 
#  BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND 
#  NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, 
#  DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, 
#  OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
#  


import copy
import datetime
import fcntl
import os
import Queue
import soco
import sys
import time


def log(*args):
    if int(os.getenv("SONOS_GM_ENABLE_LOGGING", 0)) != 0:
        print args


class EventQueue:
    def __init__(self, zones):
        self.events = {}

        for zone in zones:
            self.events[zone.ip_address] = Queue.Queue()

    def get_nowait(self):
        for ip_address, queue in self.events.iteritems():
            while not queue.empty():
                yield ip_address, queue.get_nowait() 

        yield None

    def empty(self):
        for ip_address, queue in self.events.iteritems():
            if not queue.empty():
                return False
        return True


class Topology:
    def __init__(self, groups):
        self.groups = {}

        for group in groups:
            self.groups[group.coordinator.ip_address] = [z.ip_address for z in group.members]

    def __eq__(self, other):
        if isinstance(other, self.__class__):
            return self.groups == other.groups
        return False

    def __ne__(self, other):
        return not self.__eq__(other)

    def __str__(self):
        def print_group(coordinator, members):
            return ("{" + coordinator + ": [" + 
                    ", ".join(members) + "]}")

        return ", ".join([print_group(c, m) for c, m in self.groups.iteritems()])

    def configure(self):
        log("SETTING TOPOLOGY: " + self.__str__())

        for group in self.groups:
            master = soco.SoCo(group)

            if group not in self.groups.keys():
                master.unjoin()

            for slave_address in self.groups[group]:
                if slave_address != group:
                    slave = soco.SoCo(slave_address)
                    slave.join(master)


class TopologyMonitor:
    def __init__(self, zones):
        self.events = EventQueue(zones)
        self.subscriptions = []

        for zone in zones:
            sub = zone.zoneGroupTopology.subscribe(event_queue=self.events.events[zone.ip_address])
            self.subscriptions.append(sub)
            self.zone = zone
            break

    def get_events(self):
        class TopologyEvent:
            def __init__(self, topology, temp):
                self.topology = topology
                self.temporary = temp

        for event in self.events.get_nowait():
            if event is not None:
                ip_address, data = event
                yield TopologyEvent(Topology(self.zone.all_groups), self.is_any_playing())
        yield None

    def is_any_playing(self):
        for zone in soco.discover():
            info = zone.get_current_transport_info()
            if info["current_transport_state"] == "PLAYING" and zone.is_coordinator:
                return True
        return False


class CoordinatorMonitor:
    def __init__(self, zones):
        self.events = EventQueue(zones)
        self.subscriptions = []

        for zone in zones:
            sub = zone.avTransport.subscribe(event_queue = self.events.events[zone.ip_address])
            self.subscriptions.append(sub)

    def get_events(self):
        class CoordinatorEvent:
            def __init__(self, ip_address, playing, track):
                self.ip_address = ip_address 
                self.playing = playing
                self.track = track

        for event in self.events.get_nowait():
            if event is not None:
                ip_address, data = event
                log(ip_address, data.variables)

                if ("transport_state" in data.variables.keys() and
                    "current_track_uri" in data.variables.keys()):
                    playing = data.transport_state == "PLAYING"
                    yield CoordinatorEvent(ip_address, playing, data.current_track_uri)
        yield None


class PlaybarManager:
    def __init__(self, topology):
        self.topology = topology
        self.groups = {}

    def playing(self, ip_address, track):
        if self._is_playing_tv(track):
            if self.topology is not None:
                for group in self.topology.groups:
                    if ip_address in self.topology.groups[group]:
                        if group not in self.groups:
                            self.groups[group] = set()

                        self.groups[group].add(ip_address)
                        log("Detected playbar "+ip_address+" in TV mode, coordinator: " + group)
                        break
        else:
            if ip_address in self.groups.keys() and len(self.groups[ip_address]) > 0:
                log("Merging " + ", ".join(self.groups[ip_address]) + " into group " + ip_address)

                master = soco.SoCo(ip_address)
                for slave_address in self.groups[ip_address]:
                    slave = soco.SoCo(slave_address)
                    slave.join(master)

    def stopped(self, ip_address):
        for group in self.groups:
            if ip_address in self.groups[group]:
                self.groups[group].remove(ip_address)
                log("Removed " + ip_address + " from group with coordinator " + group)
                break

    def _is_playing_tv(self, track):
        return track.startswith("x-sonos-htastream") and track.endswith("spdif")


class GroupManager:
    def __init__(self, zones, revertDelaySeconds, playbarRejoinGroupOnPlay):
        self.revertDelaySeconds = revertDelaySeconds
        self.revertTime = None

        self.topologyMonitor = TopologyMonitor(zones)
        self.coordinatorMonitor = CoordinatorMonitor(zones)

        self.topology = None
        self.tempTopology = None
        
        self.playbarRejoinGroupOnPlay = playbarRejoinGroupOnPlay != 0
        self.playbarManager = PlaybarManager(self.topology)

    def poll(self):
        self._poll_topology_events()
        self._poll_coordinator_events()
        self._poll_revert_time()

    def _poll_topology_events(self):
        for event in self.topologyMonitor.get_events():
            if event is not None:
                if event.temporary:
                    self.tempTopology = event.topology
                    log("TEMP: " + str(event.topology))
                else:
                    self.topology = event.topology
                    self.playbarManager.topology = event.topology
                    log("PERM: " + str(event.topology))

    def _poll_coordinator_events(self):
        for event in self.coordinatorMonitor.get_events():
            if event is not None:
                if not event.playing:
                    if (self.topology != self.tempTopology and
                        self.topology is not None and
                        self.tempTopology is not None and
                        self.revertTime is None):
                        now = datetime.datetime.now()
                        self.revertTime = now + datetime.timedelta(0, self.revertDelaySeconds)
                        log("Starting revert timer: " + str(self.revertDelaySeconds) + " secs")

                    if self.playbarRejoinGroupOnPlay:
                        self.playbarManager.stopped(event.ip_address)
                else:
                    if self.playbarRejoinGroupOnPlay:
                        self.playbarManager.playing(event.ip_address, event.track)

    def _poll_revert_time(self):
        if self.revertTime is not None and datetime.datetime.now() >= self.revertTime:
            if not self.topologyMonitor.is_any_playing():
                self.topology.configure()
                self.tempTopology = None
            else:
                log("Cancelled revert as zones are playing")

            self.revertTime = None


def run_group_manager():
    zones = soco.discover()
    
    if zones is None or len(zones) == 0:
        sys.stderr.write("Could not detect Sonos network!\n")
        sys.exit(1)

    for zone in zones:
        log(zone.ip_address, zone.player_name)

    revertDelay = int(os.getenv("SONOS_GM_REVERT_DELAY", 5))
    playbarRejoinGroupOnPlay = int(os.getenv("SONOS_GM_PLAYBAR_REJOIN_GROUP_ON_PLAY", 1))

    groupManager = GroupManager(zones, revertDelay, playbarRejoinGroupOnPlay)

    try:
        while True:
            groupManager.poll()
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        sys.exit(0)


def main():
    tmpPath = os.getenv("SONOS_GM_PID_FILE_PATH", "/tmp")
    fp = open(tmpPath + "/sonos-group-manager.pid", "w")

    try:
        fcntl.lockf(fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except IOError:
        sys.stderr.write("Another instance is already running!\n")
        sys.exit(1)

    run_group_manager()


main()

