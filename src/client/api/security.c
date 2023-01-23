/**
 * (C) Copyright 2023 Intel Corporation.
 *
 * SPDX-License-Identifier: BSD-2-Clause-Patent
 */

#include <daos/security.h>

int
daos_sec_get_permissions(daos_prop_t *pool_prop, daos_prop_t *cont_prop, uid_t uid, gid_t gid,
			 gid_t *supp_gids, size_t nr_supp_gids, uint64_t *perms)
{
	return dc_sec_get_user_permissions(pool_prop, cont_prop, uid, gid, supp_gids, nr_supp_gids,
					   perms);
}
