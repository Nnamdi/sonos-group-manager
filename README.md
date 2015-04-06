sonos-group-manager
===================

*sonos-group-manager* is a Python script that monitors topology changes in a Sonos network, and 
supports automatically configuring to a default topology.

Installation
------------

*sonos-group-manager* requires [SoCo](http://python-soco.com) (provided in repo), so inherits its 
dependencies.  Specifically the [Requests](http://docs.python-requests.org) HTTP library, which can
be installed with pip:

```
pip install requests
```

Basic Usage
-----------

Download and run with:

```
git clone http://github.com/Nnamdi/sonos-group-manager
cd sonos-group-manager
./sonos-group-manager
```

#### Configuring a default topology

The default topology is the grouping of zones that *sonos-group-manager* will revert to once all 
zones in the network stop playing.  To change, pause all zones in the network, and use your 
preferred controller to configure the groups.  The new configuration will be automatically 
detected, and set as the default topology.

#### Configuring a temporary topology

A temporary topology is any group of zones other than the default.  To setup, you need at least one
zone playing, then use your preferred controller to configure the groups.  The new configuration
will be automatically detected as a temporary topology.

Once all zones in the network stop playing, *sonos-group-manager* will revert the groupings back
to the default topology.

#### Playbar group management

When in TV/SPDIF input mode, the Playbar does not report that it has stopped playing for 
approximately 5 minutes after the input stops, preventing a timely change back to the default
topology.  To compensate, *sonos-group-manager* has a feature (enabled by default), that will
automatically add a Playbar back into its default group, if:

* The Playbar is playing TV/SPDIF
* The default group the playbar belongs to starts playing

License
-------

*sonos-group-manager* is released under the [MIT License](http://opensource.org/licenses/mit-license.php)
