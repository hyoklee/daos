/**
 * (C) Copyright 2022 Intel Corporation.
 *
 * SPDX-License-Identifier: BSD-2-Clause-Patent
 */

#include <daos/mem.h>
#include "dav_internal.h"
#include "wal_tx.h"
#include "util.h"

static inline uint64_t
mdblob_addr2offset(struct dav_obj *hdl, void *addr)
{
	D_ASSERT(((uintptr_t)addr >= (uintptr_t)hdl->do_base) &&
		 ((uintptr_t)addr <= ((uintptr_t)hdl->do_base + hdl->do_size)));
	return (uintptr_t)addr - (uintptr_t)hdl->do_base;
}

#define AD_TX_ACT_ADD(tx, wa)							\
	do {									\
		d_list_add_tail(&(wa)->wa_link, &(tx)->wt_redo);		\
		(tx)->wt_redo_cnt++;						\
		if ((wa)->wa_act.ac_opc == UMEM_ACT_COPY ||			\
		    (wa)->wa_act.ac_opc == UMEM_ACT_COPY_PTR) {			\
			(tx)->wt_redo_payload_len += (wa)->wa_act.ac_copy.size;	\
		} else if ((wa)->wa_act.ac_opc == UMEM_ACT_MOVE) {		\
			/* ac_move src addr is playload after wal_trans_entry */\
			(tx)->wt_redo_payload_len += sizeof(uint64_t);		\
		}								\
	} while (0)

#define AD_TX_ACT_DEL(wa)	d_list_del(wa)

/** allocate wal_action, if success the wa_link and wa_act.ac_opc will be init-ed */
#define D_ALLOC_ACT(wa, opc, size)							\
	do {										\
		if (opc == UMEM_ACT_COPY)						\
			D_ALLOC(wa, offsetof(struct wal_action,				\
					     wa_act.ac_copy.payload[size]));		\
		else									\
			D_ALLOC_PTR(wa);						\
		if (likely(wa != NULL)) {						\
			D_INIT_LIST_HEAD(&wa->wa_link);					\
			wa->wa_act.ac_opc = opc;					\
		}									\
	} while (0)

static inline void
act_copy_payload(struct umem_action *act, void *addr, daos_size_t size)
{
	char	*dst = (char *)&act->ac_copy.payload[0];

	if (size > 0)
		memcpy(dst, addr, size);
}

static int
dav_wal_tx_reinit(struct dav_obj *dav_hdl)
{
	struct dav_tx	*tx = utx2wtx(dav_hdl->do_utx);
	int		 rc = 0;

	if (dav_hdl == NULL) {
		D_FATAL("invalid argument\n");
		return -DER_INVAL;
	}
	tx->wt_id++;
	D_INIT_LIST_HEAD(&tx->wt_redo);
	tx->wt_redo_cnt = 0;
	tx->wt_redo_payload_len = 0;
	tx->wt_redo_act_pos = NULL;
	tx->wt_dav_hdl = dav_hdl;

	return rc;
}

int
dav_wal_tx_init(struct dav_obj *dav_hdl)
{
	D_ASSERT(dav_hdl != NULL);
	struct dav_tx	*tx = utx2wtx(dav_hdl->do_utx);

	memset(tx, 0, sizeof(struct dav_tx));
	return dav_wal_tx_reinit(dav_hdl);
}

static void
dav_wal_tx_act_cleanup(d_list_t *list)
{
	struct wal_action	*wa, *next;

	d_list_for_each_entry_safe(wa, next, list, wa_link) {
		d_list_del(&wa->wa_link);
		D_FREE(wa);
	}
}

static int
dav_wal_tx_push(struct dav_obj *dav_hdl, d_list_t *redo_list, uint64_t id)
{
	struct wal_action	*wa, *next;
	struct umem_action	*ua;
	struct umem_store	*store = &dav_hdl->do_store;
	struct umem_wal_tx	*utx = dav_hdl->do_utx;
	char	*pathname = basename(dav_hdl->do_path);
	int	 rc;

	/* id = utx->utx_id; */
	d_list_for_each_entry_safe(wa, next, redo_list, wa_link) {
		ua = &wa->wa_act;
		switch (ua->ac_opc) {
		case UMEM_ACT_COPY:
			D_DEBUG(DB_TRACE,
				"%s: ACT_COPY     txid=%lu, (p,o)=%lu,%lu size=%lu\n",
				pathname, id,
				ua->ac_copy.addr / PAGESIZE, ua->ac_copy.addr % PAGESIZE,
				ua->ac_copy.size);
			break;
		case UMEM_ACT_ASSIGN:
			D_DEBUG(DB_TRACE,
				"%s: ACT_ASSIGN   txid=%lu, (p,o)=%lu,%lu size=%u\n",
				pathname, id,
				ua->ac_assign.addr / PAGESIZE, ua->ac_assign.addr % PAGESIZE,
				ua->ac_assign.size);
			break;
		case UMEM_ACT_SET:
			D_DEBUG(DB_TRACE,
				"%s: ACT_SET      txid=%lu, (p,o)=%lu,%lu size=%u val=%u\n",
				pathname, id,
				ua->ac_set.addr / PAGESIZE, ua->ac_set.addr % PAGESIZE,
				ua->ac_set.size, ua->ac_set.val);
			break;
		case UMEM_ACT_SET_BITS:
			D_DEBUG(DB_TRACE,
				"%s: ACT_SET_BITS txid=%lu, (p,o)=%lu,%lu bit_pos=%u num_bits=%u\n",
				pathname, id,
				ua->ac_op_bits.addr / PAGESIZE, ua->ac_op_bits.addr % PAGESIZE,
				ua->ac_op_bits.pos, ua->ac_op_bits.num);
			break;
		case UMEM_ACT_CLR_BITS:
			D_DEBUG(DB_TRACE,
				"%s: ACT_CLR_BITS txid=%lu, (p,o)=%lu,%lu bit_pos=%u num_bits=%u\n",
				pathname, id,
				ua->ac_op_bits.addr / PAGESIZE, ua->ac_op_bits.addr % PAGESIZE,
				ua->ac_op_bits.pos, ua->ac_op_bits.num);
			break;
		default:
			D_ERROR("%s: unknown opc %d\n", dav_hdl->do_path, ua->ac_opc);
			ASSERT(0);
		}
	}
	rc = store->stor_ops->so_wal_submit(store, utx, NULL);
	return rc;
}

/** complete the wl transaction */
int
dav_wal_tx_commit(struct dav_obj *hdl)
{
	struct dav_tx	*tx = utx2wtx(hdl->do_utx);
	d_list_t		 wt_redo;
	int			 rc = 0;

	D_ASSERT(hdl != NULL);

	D_INIT_LIST_HEAD(&wt_redo);
	d_list_splice_init(&tx->wt_redo, &wt_redo);

	/* write actions in redo list to WAL */
	rc = dav_wal_tx_push(hdl, &wt_redo, tx->wt_id);
	/* FAIL the engine if commit fails */
	D_ASSERT(rc == 0);
	DAV_DBG("tx_id:%lu committed to WAL: %u bytes in %u actions",
		tx->wt_id, tx->wt_redo_payload_len, tx->wt_redo_cnt);

	dav_wal_tx_act_cleanup(&wt_redo);
	dav_wal_tx_reinit(hdl);

	return 0;
}

/**
 * snapshot data from src to either wal redo log.
 */
int
dav_wal_tx_snap(void *hdl, void *addr, daos_size_t size, void *src, uint32_t flags)
{
	struct dav_obj		*dav_hdl = (struct dav_obj *)hdl;
	struct dav_tx		*tx = utx2wtx(dav_hdl->do_utx);
	struct wal_action	*wa_redo;

	D_ASSERT(hdl != NULL);

	if (addr == NULL || size == 0 || size > UMEM_ACT_PAYLOAD_MAX_LEN)
		return -DER_INVAL;

	D_ALLOC_ACT(wa_redo, UMEM_ACT_COPY, size);
	if (wa_redo == NULL)
		return -DER_NOMEM;

	act_copy_payload(&wa_redo->wa_act, src, size);
	wa_redo->wa_act.ac_copy.addr = mdblob_addr2offset(tx->wt_dav_hdl, addr);
	wa_redo->wa_act.ac_copy.size = size;
	AD_TX_ACT_ADD(tx, wa_redo);
	return 0;
}

/** assign uint64_t value to @addr */
int
dav_wal_tx_assign(void *hdl, void *addr, uint64_t val)
{
	struct dav_obj		*dav_hdl = (struct dav_obj *)hdl;
	struct dav_tx		*tx = utx2wtx(dav_hdl->do_utx);
	struct wal_action	*wa_redo;

	D_ASSERT(hdl != NULL);
	if (addr == NULL)
		return -DER_INVAL;

	D_ALLOC_ACT(wa_redo, UMEM_ACT_ASSIGN, sizeof(uint64_t));
	if (wa_redo == NULL)
		return -DER_NOMEM;
	wa_redo->wa_act.ac_assign.addr = mdblob_addr2offset(tx->wt_dav_hdl, addr);
	wa_redo->wa_act.ac_assign.size = 8;
	wa_redo->wa_act.ac_assign.val = val;
	AD_TX_ACT_ADD(tx, wa_redo);

	return 0;
}

/** Set bits starting from pos */
int
dav_wal_tx_set_bits(void *hdl, void *addr, uint32_t pos, uint16_t num_bits)
{
	struct dav_obj		*dav_hdl = (struct dav_obj *)hdl;
	struct dav_tx		*tx = utx2wtx(dav_hdl->do_utx);
	struct wal_action	*wa_redo;

	D_ASSERT(hdl != NULL);
	if (addr == NULL)
		return -DER_INVAL;

	D_ALLOC_ACT(wa_redo, UMEM_ACT_SET_BITS, sizeof(uint64_t));
	if (wa_redo == NULL)
		return -DER_NOMEM;
	wa_redo->wa_act.ac_op_bits.addr = mdblob_addr2offset(tx->wt_dav_hdl, addr);
	wa_redo->wa_act.ac_op_bits.num = num_bits;
	wa_redo->wa_act.ac_op_bits.pos = pos;
	AD_TX_ACT_ADD(tx, wa_redo);

	return 0;
}

/** Clr bits starting from pos */
int
dav_wal_tx_clr_bits(void *hdl, void *addr, uint32_t pos, uint16_t num_bits)
{
	struct dav_obj		*dav_hdl = (struct dav_obj *)hdl;
	struct dav_tx		*tx = utx2wtx(dav_hdl->do_utx);
	struct wal_action	*wa_redo;

	D_ASSERT(hdl != NULL);
	if (addr == NULL)
		return -DER_INVAL;

	D_ALLOC_ACT(wa_redo, UMEM_ACT_CLR_BITS, sizeof(uint64_t));
	if (wa_redo == NULL)
		return -DER_NOMEM;
	wa_redo->wa_act.ac_op_bits.addr = mdblob_addr2offset(tx->wt_dav_hdl, addr);
	wa_redo->wa_act.ac_op_bits.num = num_bits;
	wa_redo->wa_act.ac_op_bits.pos = pos;
	AD_TX_ACT_ADD(tx, wa_redo);

	return 0;
}

/**
 * memset a storage region, save the operation for redo
 */
int
dav_wal_tx_set(void *hdl, void *addr, char c, daos_size_t size)
{
	struct dav_obj		*dav_hdl = (struct dav_obj *)hdl;
	struct dav_tx		*tx = utx2wtx(dav_hdl->do_utx);
	struct wal_action	*wa_redo;

	D_ASSERT(hdl != NULL);

	if (addr == NULL || size == 0 || size > UMEM_ACT_PAYLOAD_MAX_LEN)
		return -DER_INVAL;

	D_ALLOC_ACT(wa_redo, UMEM_ACT_SET, size);
	if (wa_redo == NULL)
		return -DER_NOMEM;

	wa_redo->wa_act.ac_set.addr = mdblob_addr2offset(tx->wt_dav_hdl, addr);
	wa_redo->wa_act.ac_set.size = size;
	wa_redo->wa_act.ac_set.val = c;
	AD_TX_ACT_ADD(tx, wa_redo);
	return 0;
}

/**
 * query action number in redo list.
 */
uint32_t
wal_tx_act_nr(struct umem_wal_tx *utx)
{
	struct dav_tx *tx = utx2wtx(utx);

	return tx->wt_redo_cnt;
}

/**
 * query payload length in redo list.
 */
uint32_t
wal_tx_payload_len(struct umem_wal_tx *utx)
{
	struct dav_tx *tx = utx2wtx(utx);

	return tx->wt_redo_payload_len;
}

/**
 * get first action pointer, NULL for list empty.
 */
struct umem_action *
wal_tx_act_first(struct umem_wal_tx *utx)
{
	struct dav_tx *tx = utx2wtx(utx);

	if (d_list_empty(&tx->wt_redo)) {
		tx->wt_redo_act_pos = NULL;
		return NULL;
	}

	tx->wt_redo_act_pos = dav_action_get_next(tx->wt_redo);
	return &tx->wt_redo_act_pos->wa_act;
}

/**
 * get next action pointer, NULL for done or list empty.
 */
struct umem_action *
wal_tx_act_next(struct umem_wal_tx *utx)
{
	struct dav_tx *tx = utx2wtx(utx);

	if (tx->wt_redo_act_pos == NULL) {
		if (d_list_empty(&tx->wt_redo))
			return NULL;
		tx->wt_redo_act_pos = dav_action_get_next(tx->wt_redo);
		return &tx->wt_redo_act_pos->wa_act;
	}

	D_ASSERT(!d_list_empty(&tx->wt_redo));
	tx->wt_redo_act_pos = dav_action_get_next(tx->wt_redo_act_pos->wa_link);
	if (&tx->wt_redo_act_pos->wa_link == &tx->wt_redo) {
		tx->wt_redo_act_pos = NULL;
		return NULL;
	}
	return &tx->wt_redo_act_pos->wa_act;
}
