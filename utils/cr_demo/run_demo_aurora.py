"""
  (C) Copyright 2023 Intel Corporation.

  SPDX-License-Identifier: BSD-2-Clause-Patent
"""
import argparse
import time
import subprocess
import yaml
import re
from collections import defaultdict
from ClusterShell.NodeSet import NodeSet
from demo_utils import format_storage, inject_fault_mgmt, list_pool, check_enable,\
    check_start, check_disable, repeat_check_query, check_repair, create_uuid_to_seqnum,\
    pool_get_prop, create_pool, inject_fault_pool, create_container, inject_fault_daos,\
    system_stop, system_query, storage_query_usage, cont_get_prop, system_start,\
    check_set_policy


test_cmd = f"sudo date"
test_cmd_list = test_cmd.split(" ")
print(f"Check sudo works by calling: {test_cmd}")
subprocess.run(test_cmd_list, check=False)

# Need to use at least "scm_size: 15" for server config to create 8 1GB-pools.
POOL_SIZE = "100GB"
POOL_SIZE_F5 = "100GB"
POOL_LABEL = "tank"
CONT_LABEL = "bucket"

print("\nF1: Dangling pool")
print("F2: Lost the majority of pool service replicas")
print("F3: Orphan pool")
print("F4: Inconsistent pool label between MS and PS")
print("F5: Orphan pool shard")
print("F6: Dangling pool map")
print("F7: Orphan container")
print("F8: Inconsistent container label between CS and container property")

PARSER = argparse.ArgumentParser()
PARSER.add_argument("-l", "--hostlist", required=True, help="List of hosts to format")
ARGS = vars(PARSER.parse_args())

HOSTLIST = ARGS["hostlist"]

print(f"\n1. Format storage on {HOSTLIST}.")
format_storage(host_list=HOSTLIST)

print("\nWait for 15 sec for format...")
time.sleep(15)

# Call dmg system query to obtain the IP address of necessary ranks.
rank_to_ip = {}
stdout = system_query(json=True)
print(f"dmg system query stdout = {stdout}")
generated_yaml = yaml.safe_load(stdout)
for member in generated_yaml["response"]["members"]:
    rank_to_ip[member["rank"]] = member["addr"].split(":")[0]

# Create rank to mount point map and host to ranks map for F2 and F5.
# 1. scp daos_control.log from all nodes to here, where this script runs. scp the local
# file as well. Add hostname to the end of the file name. The log contains rank and PID.
node_set = NodeSet(HOSTLIST)
hostlist = list(node_set)
# Number of nodes used for F2.
node_count = 2
print(f"## Use first {node_count} nodes in {hostlist}")
for i in range(node_count):
    scp_cmd_list = ["scp", f"{hostlist[i]}:/var/tmp/daos_testing/daos_control.log",
                    f"/var/tmp/daos_testing/daos_control_{hostlist[i]}.log"]
    subprocess.run(scp_cmd_list, check=False)

# 2. Determine the rank to PID mapping from the control logs. In addition, determine the
# host to ranks mapping for creating the pool. We need to know the four ranks for the
# first two nodes. We'll use many nodes in Aurora, but only two nodes for F2.
rank_to_pid = {}
host_to_ranks = defaultdict(list)
SEARCH_STR = r"DAOS I/O Engine.*process (\d+) started on rank (\d+)"
for i in range(node_count):
    with open(
        f"/var/tmp/daos_testing/daos_control_{hostlist[i]}.log", "r",
        encoding="utf-8") as file:
        for line in file:
            match = re.findall(SEARCH_STR, line)
            if match:
                print(match)
                pid = int(match[0][0])
                rank = int(match[0][1])
                rank_to_pid[rank] = pid
                host_to_ranks[hostlist[i]].append(rank)
print(f"## rank_to_pid = {rank_to_pid}")
print(f"## host_to_ranks = {host_to_ranks}")

# 3. Determine the PID to mount point mapping by calling ps ax and search for daos_engine.
# Sample line:
# 84877 ?        SLl  102:04 /usr/bin/daos_engine -t 8 -x 1 -g daos_server -d
# /var/run/daos_server -T 2 -n /mnt/daos1/daos_nvme.conf -p 1 -I 1 -r 8192 -H 2 -s
# /mnt/daos1
pid_to_mount = {}
mount_0 = "/mnt/daos0"
mount_1 = "/mnt/daos1"
for i in range(node_count):
    clush_ps_ax = ["clush", "-w", hostlist[i], "ps ax"]
    result = subprocess.check_output(clush_ps_ax)
    result_list = result.decode("utf-8").split("\n")
    for result in result_list:
        if "daos_engine" in result:
            print(result)
            if mount_0 in result:
                pid = re.split(r"\s+", result)[1]
                print(f"## pid for {mount_0} = {pid}")
                pid = int(pid)
                pid_to_mount[pid] = mount_0
            elif mount_1 in result:
                pid = re.split(r"\s+", result)[1]
                print(f"## pid for {mount_1} = {pid}")
                pid = int(pid)
                pid_to_mount[pid] = mount_1
print(f"## pid_to_mount = {pid_to_mount}")

# 4. Determine the four ranks in hostlist[0] and hostlist[1] to create F2 pool.
f2_ranks = []
f2_ranks.extend(host_to_ranks[hostlist[0]])
f2_ranks.extend(host_to_ranks[hostlist[1]])
# Ranks in the map are int, so convert them to string and separate them with comma.
f2_ranks_str = ""
for rank in f2_ranks:
    if f2_ranks_str == "":
        f2_ranks_str = str(rank)
    else:
        f2_ranks_str += "," + str(rank)
print(f"## f2_ranks_str = {f2_ranks_str}")

# 5. Determine the two ranks in hostlist[0] to create F5 pool.
f5_ranks = []
f5_ranks.extend(host_to_ranks[hostlist[0]])
# Ranks in the map are int, so convert them to string and separate them with comma.
f5_ranks_str = ""
for rank in f5_ranks:
    if f5_ranks_str == "":
        f5_ranks_str = str(rank)
    else:
        f5_ranks_str += "," + str(rank)
print(f"## f5_ranks_str = {f5_ranks_str}")

# Set up variables to copy pool directory locally. This will crash the node.
# f5_pool_rank = host_to_ranks[hostlist[0]][0]
# f5_no_pool_rank = host_to_ranks[hostlist[0]][1]
# print(f"## f5_pool_rank = {f5_pool_rank}; f5_no_pool_rank = {f5_no_pool_rank}")

# Add input here to make sure all ranks are joined before starting the script.
input("\n2. Create 8 pools and containers. Hit enter...")
POOL_LABEL_1 = POOL_LABEL + "_F1"
POOL_LABEL_2 = POOL_LABEL + "_F2"
POOL_LABEL_3 = POOL_LABEL + "_F3"
POOL_LABEL_4 = POOL_LABEL + "_F4"
POOL_LABEL_5 = POOL_LABEL + "_F5"
POOL_LABEL_6 = POOL_LABEL + "_F6"
POOL_LABEL_7 = POOL_LABEL + "_F7"
POOL_LABEL_8 = POOL_LABEL + "_F8"
CONT_LABEL_7 = CONT_LABEL + "_F7"
CONT_LABEL_8 = CONT_LABEL + "_F8"

# F1. CIC_POOL_NONEXIST_ON_ENGINE - dangling pool
create_pool(pool_size=POOL_SIZE, pool_label=POOL_LABEL_1)
# F2. CIC_POOL_LESS_SVC_WITHOUT_QUORUM
create_pool(pool_size=POOL_SIZE, pool_label=POOL_LABEL_2, ranks=f2_ranks_str, nsvc="3")
# F3. CIC_POOL_NONEXIST_ON_MS - orphan pool
create_pool(pool_size=POOL_SIZE, pool_label=POOL_LABEL_3)
# F4. CIC_POOL_BAD_LABEL - inconsistent pool label between MS and PS
create_pool(pool_size=POOL_SIZE, pool_label=POOL_LABEL_4)
# F5. CIC_ENGINE_NONEXIST_IN_MAP - orphan pool shard
create_pool(pool_size=POOL_SIZE_F5, pool_label=POOL_LABEL_5, ranks=f5_ranks_str)
# F6. CIC_ENGINE_HAS_NO_STORAGE - dangling pool map
create_pool(pool_size=POOL_SIZE, pool_label=POOL_LABEL_6)
# F7. CIC_CONT_NONEXIST_ON_PS - orphan container
create_pool(pool_size=POOL_SIZE, pool_label=POOL_LABEL_7)
create_container(pool_label=POOL_LABEL_7, cont_label=CONT_LABEL_7)
print()
# F8. CIC_CONT_BAD_LABEL
create_pool(pool_size=POOL_SIZE, pool_label=POOL_LABEL_8)
create_container(pool_label=POOL_LABEL_8, cont_label=CONT_LABEL_8)

print("(Create label to UUID mapping.)")
label_to_uuid = {}
stdout = list_pool(json=True)
generated_yaml = yaml.safe_load(stdout)
for pool in generated_yaml["response"]["pools"]:
    label_to_uuid[pool["label"]] = pool["uuid"]

print(f"\n3-F5. Print storage usage to show original usage of {POOL_LABEL_5}. "
      f"Pool is created on {hostlist[0]}.")
# F5 pool is created on hostlist[0] ranks, but we'll copy the pool dir from there to one
# of the ranks in hostlist[1], so show both.
f5_host_list = f"{hostlist[0]},{hostlist[1]}"
storage_query_usage(host_list=f5_host_list)

####################################################################
print("\n4. Inject fault with dmg for F1, F3, F4, F7, F8.")
# F1
inject_fault_pool(pool_label=POOL_LABEL_1, fault_type="CIC_POOL_NONEXIST_ON_ENGINE")

# F3
inject_fault_mgmt(pool_label=POOL_LABEL_3, fault_type="CIC_POOL_NONEXIST_ON_MS")

# F4
inject_fault_mgmt(pool_label=POOL_LABEL_4, fault_type="CIC_POOL_BAD_LABEL")

# F7
inject_fault_daos(
    pool_label=POOL_LABEL_7, cont_label=CONT_LABEL_7, fault_type="DAOS_CHK_CONT_ORPHAN")

# F8
inject_fault_daos(
    pool_label=POOL_LABEL_8, cont_label=CONT_LABEL_8,
    fault_type="DAOS_CHK_CONT_BAD_LABEL")

####################################################################
input("\n5-1. Stop servers to manipulate for F2, F5, F6, F7. Hit enter...")
system_stop()

# F2: Destroy tank_2 rdb-pool on rank 0 and 2.
rank_0_ip = rank_to_ip[0]
rank_2_ip = rank_to_ip[2]
rank_0_mount = pid_to_mount[rank_to_pid[0]]
rank_2_mount = pid_to_mount[rank_to_pid[2]]
rm_rank_0 = f"sudo rm {rank_0_mount}/{label_to_uuid[POOL_LABEL_2]}/rdb-pool"
rm_rank_2 = f"sudo rm {rank_2_mount}/{label_to_uuid[POOL_LABEL_2]}/rdb-pool"
clush_rm_rank_0 = ["clush", "-w", rank_0_ip, rm_rank_0]
clush_rm_rank_2 = ["clush", "-w", rank_2_ip, rm_rank_2]
print("(F2: Destroy tank_2 rdb-pool on rank 0 and 2.)")
print(f"Command for rank 0: {clush_rm_rank_0}\n")
print(f"Command for rank 2: {clush_rm_rank_2}\n")
subprocess.run(clush_rm_rank_0, check=False)
subprocess.run(clush_rm_rank_2, check=False)

# F5: Copy tank_5 pool directory from /mnt/daos1 in hostlist[0] to /mnt/daos0 in
# hostlist[1]. Match owner. (Mount points are arbitrary.)
# In order to scp the pool directory without password, there are two things to set up.
# 1. Since we're running scp as user, update the mode of the source pool directory as
# below.
# Set 777 for /mnt/daos1 and /mnt/daos1/<pool_5>/* i.e.,
# chmod 777 /mnt/daos1; chmod -R 777 /mnt/daos1/<pool_5>
# 2. Update mode of the destination mount point to 777. e.g.,
# clush -w <dst_host> "sudo chmod 777 /mnt/daos0"

# Alternatively, we can generate public-private key pair for root and call scp with sudo.
# Then we don't need to do step 2 (update mode to 777).

print("(F5: Update mode of the source pool directory.)")
pool_uuid_5 = label_to_uuid[POOL_LABEL_5]
chmod_cmd = f"sudo chmod 777 /mnt/daos1; sudo chmod -R 777 /mnt/daos1/{pool_uuid_5}"
clush_chmod_cmd = ["clush", "-w", hostlist[0], chmod_cmd]
print(f"Command: {clush_chmod_cmd}\n")
subprocess.run(clush_chmod_cmd, check=False)

print("(F5: Update mode of the destination mount point.)")
chmod_cmd = f"sudo chmod 777 /mnt/daos0"
clush_chmod_cmd = ["clush", "-w", hostlist[1], chmod_cmd]
print(f"Command: {clush_chmod_cmd}\n")
subprocess.run(clush_chmod_cmd, check=False)

# Run the following scp command on hostlist[0] using clush:
# scp -rp /mnt/daos1/<pool_uuid_5> root@hostlist[1]:/mnt/daos/
print(f"(F5: Copy pool directory from {hostlist[0]} to {hostlist[1]}.)")
scp_cmd = (f"scp -rp /mnt/daos1/{pool_uuid_5} "
           f"{hostlist[1]}:/mnt/daos0/")
copy_pool_dir = ["clush", "-w", hostlist[0], scp_cmd]
print(f"Command: {copy_pool_dir}\n")
subprocess.run(copy_pool_dir, check=False)

print("(F5: Set owner for the copied dir and files to daos_server:daos_server.)")
chown_cmd = f"sudo chown -R daos_server:daos_server /mnt/daos0/{pool_uuid_5}"
clush_chown_cmd = ["clush", "-w", hostlist[1], chown_cmd]
print(f"Command: {clush_chown_cmd}\n")
subprocess.run(clush_chown_cmd, check=False)


# Copy pool directory from one mount point to another locally. This will crash the node.
# f5_pool_mount = pid_to_mount[rank_to_pid[f5_pool_rank]]
# f5_no_pool_mount = pid_to_mount[rank_to_pid[f5_no_pool_rank]]
# print(
#     f"(F5: Copy F5 pool dir from {f5_pool_mount} to {f5_no_pool_mount} on {hostlist[0]})")
# pool_uuid_5 = label_to_uuid[POOL_LABEL_5]
# cp_cmd = (f"sudo cp -rp {f5_pool_mount}/{pool_uuid_5} {f5_no_pool_mount}/{pool_uuid_5}")
# clush_cp_cmd = ["clush", "-w", hostlist[0], cp_cmd]
# print(f"Command: {clush_cp_cmd}\n")
# subprocess.run(clush_cp_cmd, check=False)

# print(f"(F5: Set owner for the copied dir and files to daos_server:daos_server.)")
# chown_cmd = f"sudo chown -R daos_server:daos_server {f5_no_pool_mount}/{pool_uuid_5}"
# clush_chown_cmd = ["clush", "-w", hostlist[0], chown_cmd]
# print(f"Command: {clush_chown_cmd}\n")
# subprocess.run(clush_chown_cmd, check=False)


print("(F6: Remove vos-0 from one of the nodes.)")
pool_uuid_6 = label_to_uuid[POOL_LABEL_6]
rm_cmd = f"sudo rm -rf /mnt/daos0/{pool_uuid_6}/vos-0"
# Remove vos-0 from /mnt/daos0 in rank 0 node. Note that /mnt/daos0 may not be mapped to
# rank 0. Rank 0 is mapped to either daos0 or daos1. However, we don't care for the
# purpose of testing dangling pool map.
clush_rm_cmd = ["clush", "-w", rank_to_ip[0], rm_cmd]
print(f"Command: {clush_rm_cmd}\n")
# print(f"Command: {rm_cmd}\n")
subprocess.run(clush_rm_cmd, check=False)

print("F7: Use ddb to show that the container is left in shards.")
pool_uuid_7 = label_to_uuid[POOL_LABEL_7]
# Run ddb on /mnt/daos0 of rank 0 node.
ddb_cmd = f"sudo ddb -R \"ls\" /mnt/daos0/{pool_uuid_7}/vos-0"
clush_ddb_cmd = ["clush", "-w", rank_to_ip[0], ddb_cmd]
# print(f"Command: {clush_ddb_cmd}")
# test_cmd = "sudo ls -l"
# test_cmd_list = " ".join(test_cmd)
# print("Running test command: {}".format(test_cmd_list))
# subprocess.run(test_cmd_list, check=False)
print(f"Command str: {ddb_cmd}")
ddb_cmd_list = ddb_cmd.split(" ")
print(f"Command list: {ddb_cmd_list}")
subprocess.run(ddb_cmd_list, check=False)

# (optional) F3: Show pool directory at mount point to verify that the pool exists on
# engine.

print("\n5-2. Restart servers.")
system_start()

####################################################################
input("\n6. Show the faults injected for each pool/container for F1, F3, F4, F5, F8. "
      "Hit enter...")
print(f"6-F1. Show dangling pool entry for {POOL_LABEL_1}.")
# F3 part 1
print(f"6-F3. MS doesn't recognize {POOL_LABEL_3}.")
# F4 part 1
print(f"6-F4-1. Label ({POOL_LABEL_4}) in MS are corrupted with -fault added.")
list_pool(no_query=True)

# F2: (optional) Try to create a container, which will hang.

# F4 part 2
print(f"\n6-F4-2. Label ({POOL_LABEL_4}) in PS is still original.")
POOL_LABEL_4_FAULT = POOL_LABEL_4 + "-fault"
pool_get_prop(pool_label=POOL_LABEL_4_FAULT, properties="label")

# F5: Call dmg storage query usage to show that the pool is using more space.
print(f"\n6-F5. Print storage usage to show that {POOL_LABEL_5} is using more space. "
      f"Pool directory is copied to {hostlist[1]}.")
storage_query_usage(host_list=f5_host_list)

# F8: Show inconsistency by getting the container label.
print("\n6-F8. Show container label inconsistency.")
cont_get_prop(pool_label=POOL_LABEL_8, cont_label=CONT_LABEL_8)
print(f"Error because container ({CONT_LABEL_8}) doesn't exist on container service.\n")

print(f"Container ({CONT_LABEL_8}) exists on pool service.")
cont_get_prop(pool_label=POOL_LABEL_8, cont_label="new-label", properties="label")

####################################################################
input("\n7. Enable checker. Hit enter...")
check_enable()

input("\n8. Start checker with interactive mode. Hit enter...")
check_set_policy(all_interactive=True)
print()
check_start()
print()
repeat_check_query()

####################################################################
input("\n8-1. Select repair options for F1 to F4. Hit enter...")
print("(Create UUID to sequence number.)")
uuid_to_seqnum = create_uuid_to_seqnum()
SEQ_NUM_1 = str(hex(uuid_to_seqnum[label_to_uuid[POOL_LABEL_1]]))
SEQ_NUM_2 = str(hex(uuid_to_seqnum[label_to_uuid[POOL_LABEL_2]]))
SEQ_NUM_3 = str(hex(uuid_to_seqnum[label_to_uuid[POOL_LABEL_3]]))
SEQ_NUM_4 = str(hex(uuid_to_seqnum[label_to_uuid[POOL_LABEL_4]]))
SEQ_NUM_5 = str(hex(uuid_to_seqnum[label_to_uuid[POOL_LABEL_5]]))
SEQ_NUM_6 = str(hex(uuid_to_seqnum[label_to_uuid[POOL_LABEL_6]]))
SEQ_NUM_7 = str(hex(uuid_to_seqnum[label_to_uuid[POOL_LABEL_7]]))
SEQ_NUM_8 = str(hex(uuid_to_seqnum[label_to_uuid[POOL_LABEL_8]]))

# F1: 1: Discard the dangling pool entry from MS [suggested].
print(f"\n{POOL_LABEL_1} - 1: Discard the dangling pool entry from MS [suggested].")
check_repair(sequence_num=SEQ_NUM_1, action="1")

# F2: 2: Start pool service under DICTATE mode from rank 1 [suggested].
print(f"\n{POOL_LABEL_2} - 2: Start pool service under DICTATE mode from rank 1 "
      f"[suggested].")
check_repair(sequence_num=SEQ_NUM_2, action="2")

# F3:
print(f"\n{POOL_LABEL_3} - 2: Re-add the orphan pool back to MS [suggested].")
check_repair(sequence_num=SEQ_NUM_3, action="2")

# F4: 2: Trust PS pool label.
print(f"\n{POOL_LABEL_4} - 2: Trust PS pool label.")
check_repair(sequence_num=SEQ_NUM_4, action="2")

print()
# Call dmg check query until n is entered.
repeat_check_query()

input("\n8-2. Select repair options for F5 to F8. Hit enter...")
# F5: 1: Discard the orphan pool shard to release space [suggested].
print(f"\n{POOL_LABEL_5} - 1: Discard the orphan pool shard to release space "
      f"[suggested].")
check_repair(sequence_num=SEQ_NUM_5, action="1")

# F6: 1: Change pool map for the dangling map entry [suggested].
print(f"\n{POOL_LABEL_6} - 1: Change pool map for the dangling map entry [suggested].")
check_repair(sequence_num=SEQ_NUM_6, action="1")

# F7: 1: Destroy the orphan container to release space [suggested].
print(f"\n{POOL_LABEL_7} - 1: Destroy the orphan container to release space [suggested].")
check_repair(sequence_num=SEQ_NUM_7, action="1")

# F8: 2: Trust the container label in container property.
print(f"\n{POOL_LABEL_8} - 2: Trust the container label in container property.")
check_repair(sequence_num=SEQ_NUM_8, action="2")

print()
# Call dmg check query until n is entered.
repeat_check_query()

print("\n9. Disable the checker.")
check_disable()

print("\nRun show_fixed.py to show the issues fixed...")