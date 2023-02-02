"""
  (C) Copyright 2020-2023 Intel Corporation.

  SPDX-License-Identifier: BSD-2-Clause-Patent
"""
from datetime import datetime
import time

from ClusterShell.NodeSet import NodeSet

from apricot import TestWithServers
from general_utils import get_journalctl
from run_utils import run_remote


def journalctl_time():
    """Get now() formatted for journalctl."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Daos12513(TestWithServers):
    """DAOS-12513 manual test

    :avocado: recursive
    """

    def server_journalctl(self, since):
        """Get daos_server journalctl"""
        results = get_journalctl(
            hosts=self.hostlist_servers, since=since, until=journalctl_time(),
            journalctl_type="daos_server")
        self.log.info("journalctl results = %s", results)

    def test_daos_12513(self):
        """JIRA ID: DAOS-12513

        :avocado: tags=all,manual
        :avocado: tags=vm
        :avocado: tags=control
        :avocado: tags=test_daos_12513
        """
        dmg = self.get_dmg_command()

        # Create pool
        t_before_pool_create = journalctl_time()
        pool = self.get_pool()

        # Query pool
        pool.query()

        # Check system logs after pool creation
        self.server_journalctl(t_before_pool_create)

        # Stop single rank
        t_before_stop_rank = journalctl_time()
        self.server_managers[0].stop_ranks([2], self.d_log)

        # Check system state
        dmg.system_query()

        # Check system logs for rank excluded message and rebuild completion
        self.server_journalctl(t_before_stop_rank)
        pool.wait_for_rebuild_to_start()
        pool.wait_for_rebuild_to_end()

        # Restart rank
        t_before_restart_rank = journalctl_time()
        dmg.system_start(ranks="2")
        dmg.system_query()
        time.sleep(5)
        self.server_journalctl(t_before_restart_rank)

        # Reintegrate rank
        t_before_reintegrate = journalctl_time()
        dmg.pool_list()
        pool.reintegrate("2")
        pool.wait_for_rebuild_to_start()
        pool.wait_for_rebuild_to_end()

        # Check system logs for no errors
        self.server_journalctl(t_before_reintegrate)

        # Exclude 2 ranks
        t_before_exclude = journalctl_time()
        pool.exclude([2, 3])

        # Check system state
        dmg.system_query()

        # Check system logs for rank excluded message and rebuild completion
        pool.wait_for_rebuild_to_start()
        pool.wait_for_rebuild_to_end()
        self.server_journalctl(t_before_exclude)

        # Reintegrate 2 ranks
        t_before_reintegrate = journalctl_time()
        pool.reintegrate("2")
        pool.reintegrate("3")
        pool.wait_for_rebuild_to_start()
        pool.wait_for_rebuild_to_end()
        self.server_journalctl(t_before_reintegrate)

        # Crash a server
        t_before_kill = journalctl_time()
        kill_cmd = "sudo -n pkill daos_server --signal KILL"
        if not run_remote(self.log, NodeSet(self.hostlist_servers[-1]), kill_cmd):
            self.fail("failed to pkill daos_server")
        pool.wait_for_rebuild_to_start()
        pool.wait_for_rebuild_to_end()
        self.server_journalctl(t_before_kill)

        # Restart server
        t_before_restart = journalctl_time()
        self.server_managers[0].restart([self.hostlist_servers[-1]], wait=True)
        self.server_journalctl(t_before_restart)

        # Reintegrate rank
        t_before_reintegrate = journalctl_time()
        for rank in range(len(self.hostlist_servers) * 2):
            pool.reintegrate(str(rank))
        self.server_journalctl(t_before_reintegrate)

        # Stop/start all ranks
        t_before_stop_rank = journalctl_time()
        for rank in range(len(self.hostlist_servers) * 2):
            dmg.system_stop(ranks=str(rank))
            dmg.system_start(ranks=str(rank))
            time.sleep(5)
        self.server_journalctl(t_before_stop_rank)
