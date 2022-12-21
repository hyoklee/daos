"""
  (C) Copyright 2022 Intel Corporation.

  SPDX-License-Identifier: BSD-2-Clause-Patent
"""
import subprocess
import yaml
from demo_utils import list_pool, pool_get_prop, create_container, system_stop,\
      system_query, storage_query_usage, cont_get_prop, pool_query

# Need to use at least "scm_size: 15" for server config to create 8 1GB-pools.
POOL_SIZE_1GB = "1GB"
POOL_LABEL = "tank"
CONT_LABEL = "bucket"
# Rank to create F5 pool.
F5_RANK_ORIG = 1
# Rank to copy the F5 pool dir to.
F5_RANK_FAULT = 3

# Call dmg system query to obtain the IP address of necessary ranks.
rank_to_ip = {}
stdout = system_query(json=True)
print(f"dmg system query stdout = {stdout}")
generated_yaml = yaml.safe_load(stdout)
for member in generated_yaml["response"]["members"]:
    rank_to_ip[member["rank"]] = member["addr"].split(":")[0]

# Add input here to make sure all ranks are joined before starting the script.
POOL_LABEL_1 = POOL_LABEL + "_F1"
POOL_LABEL_2 = POOL_LABEL + "_F2"
POOL_LABEL_3 = POOL_LABEL + "_F3"
POOL_LABEL_4 = POOL_LABEL + "_F4"
POOL_LABEL_5 = POOL_LABEL + "_F5"
POOL_LABEL_6 = POOL_LABEL + "_F6"
POOL_LABEL_7 = POOL_LABEL + "_F7"
POOL_LABEL_8 = POOL_LABEL + "_F8"
CONT_LABEL_8 = CONT_LABEL + "_F8"

print("(Create label to UUID mapping.)")
label_to_uuid = {}
stdout = list_pool(json=True)
generated_yaml = yaml.safe_load(stdout)
for pool in generated_yaml["response"]["pools"]:
    label_to_uuid[pool["label"]] = pool["uuid"]

input("\n10. Show the issues fixed. Hit enter...")
print(f"10-F1. Dangling pool ({POOL_LABEL_1}) was removed.")
print(f"10-F3. Orphan pool ({POOL_LABEL_3}) was reconstructed.")
list_pool()

print(f"10-F2. Create a container on {POOL_LABEL_2}. Pool can be started now, so it "
      f"should succeed.")
CONT_LABEL_2 = CONT_LABEL + "_2"
create_container(pool_label=POOL_LABEL_2, cont_label=CONT_LABEL_2)
# (optional) Show that rdb-pool file in rank 0 and 2 are recovered.

print(f"\n10-F4. Label inconsistency for {POOL_LABEL_4} was resolved. "
      f"See pool list above.")
pool_get_prop(pool_label=POOL_LABEL_4, properties="label")

# F5: Call dmg storage query usage to verify the storage was reclaimed. - Not working due
# to a bug. Instead, show that pool directory on dst node (rank 3 for 4-VM) was removed.
print(f"\n10-F5-1. Print storage usage to show that storage used by {POOL_LABEL_5} is "
      f"reclaimed after pool directory is removed from {rank_to_ip[F5_RANK_FAULT]}.")
f5_host_list = f"{rank_to_ip[F5_RANK_ORIG]},{rank_to_ip[F5_RANK_FAULT]}"
storage_query_usage(host_list=f5_host_list)

print(f"\n10-F5-2. {label_to_uuid[POOL_LABEL_5]} pool directory on rank 3 "
      f"({rank_to_ip[3]}) was removed.")
LS_CMD = "sudo ls /mnt/daos"
clush_ls_cmd = ["clush", "-w", rank_to_ip[3], LS_CMD]
print(f"Command: {clush_ls_cmd}\n")
subprocess.run(clush_ls_cmd, check=False)

print(f"\n10-F6. {POOL_LABEL_6} has one less ranks (4 -> 3).")
pool_query(pool_label=POOL_LABEL_6)
# (optional) Reintegrate rank 1 on pool 6. Wait for rebuild to finish. Then verify the
# target count.

# F8: Verify that the inconsistency is fixed. The label is back to the original.
print(f"\n10-F8. Container label inconsistency for {CONT_LABEL_8} was fixed.")
cont_get_prop(pool_label=POOL_LABEL_8, cont_label=CONT_LABEL_8, properties="label")

# F7: Stop server. Call the same ddb command to verify that the container is removed from
# shard.
print(f"\n10-F7. Use ddb to verify that the container in {POOL_LABEL_8} is removed "
      f"from shards.")
system_stop()
pool_uuid_7 = label_to_uuid[POOL_LABEL_7]
ddb_cmd = f"sudo ddb -R \"ls\" /mnt/daos/{pool_uuid_7}/vos-0"
clush_ddb_cmd = ["clush", "-w", rank_to_ip[0], ddb_cmd]
print(f"Command: {clush_ddb_cmd}")
subprocess.run(clush_ddb_cmd, check=False)
