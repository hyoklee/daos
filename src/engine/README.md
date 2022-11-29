# DAOS Data Plane (aka daos_engine)

## Module Interface

The I/O Engine supports a module interface that allows to load server-side code on demand. Each module is effectively a library dynamically loaded by the I/O Engine via dlopen.
The interface between the module and the I/O Engine is defined in the `dss_module` data structure.

Each module should specify:
- a module name
- a module identifier from `daos_module_id`
- a feature bitmask
- a module initialization and finalize function

In addition, a module can optionally configure:
- a setup and cleanup function invoked once the overall stack is up and running
- CART RPC handlers
- dRPC handlers

## Thread Model & Argobot Integration

The I/O Engine is a multi-threaded process using Argobots for non-blocking processing.

By default, one main xstream and no offload xstreams are created per target. The actual number of offload xstream can be configured through daos_engine command line parameters. Moreover, an extra xstream is created to handle incoming metadata requests. Each xstream is bound to a specific CPU core. The main xstream is the one receiving incoming target requests from both client and the other servers. A specific ULT is started to make progress on network and NVMe I/O operations.

## Thread-local Storage (TLS)

Each xstream allocates private storage that can be accessed via the `dss_tls_get()` function. When registering, each module can specify a module key with a size of data structure that will be allocated by each xstream in the TLS. The `dss_module_key_get()` function will return this data structure for a specific registered module key.

## Incast Variable Integration

DAOS uses IV (incast variable) to share values and statuses among servers under a single IV namespace, which is organized as a tree. The tree root is called IV leader, and servers can either be leaves or non-leaves. Each server maintains its own IV cache. During fetch, if the local cache can not fulfill the request, it forwards the request to its parents, until reaching the root (IV leader). As for update, it updates its local cache first, then forwards to its parents until it reaches the root, which then propagate the changes to all the other servers. The IV namespace is per pool, which is created during pool connection, and destroyed during pool disconnection. To use IV, each user needs to register itself under the IV namespace to get an identification, then it will use this ID to fetch or update its own IV value under the IV namespace.

## dRPC Server

The I/O Engine includes a dRPC server that listens for activity on a given Unix Domain Socket. See the [dRPC documentation](../control/drpc/README.md) for more details on the basics of dRPC, and the low-level APIs in Go and C.

The dRPC server polls periodically for incoming client connections and requests. It can handle multiple simultaneous client connections via the `struct drpc_progress_context` object, which manages the `struct drpc` objects for the listening socket as well as any active client connections.

The server loop runs in its own User-Level Thread (ULT) in xstream 0. The dRPC socket has been set up as non-blocking and polling uses timeouts of 0, which allows the server to run in a ULT rather than its own xstream. This channel is expected to be relatively low-traffic.

### dRPC Progress

`drpc_progress` represents one iteration of the dRPC server loop. The workflow is as follows:

1. Poll with a timeout on the listening socket and any open client connections simultaneously.
2. If any activity is seen on a client connection:
    1. If data has come in: Call `drpc_recv` to process the incoming data.
    2. If the client has disconnected or the connection has been broken: Free the `struct drpc` object and remove it from the `drpc_progress_context`.
3. If any activity is seen on the listener:
    1. If a new connection has come in: Call `drpc_accept` and add the new `struct drpc` object to the client connection list in the `drpc_progress_context`.
    2. If there was an error: Return `-DER_MISC` to the caller. This causes an error to be logged in the I/O Engine, but does not interrupt the dRPC server loop. Getting an error on the listener is unexpected.
4. If no activity was seen, return `-DER_TIMEDOUT` to the caller. This is purely for debugging purposes. In practice the I/O Engine ignores this error code, since lack of activity is not actually an error case.

### dRPC Handler Registration

Individual DAOS modules may implement handling for dRPC messages by registering a handler function for one or more dRPC module IDs.

Registering handlers is simple. In the `dss_server_module` field `sm_drpc_handlers`, statically allocate an array of `struct dss_drpc_handler` with the last item in the array zeroed out to indicate the end of the list. Setting the field to NULL indicates nothing to register. When the I/O Engine loads the DAOS module, it will register all of the dRPC handlers automatically.

**Note:** The dRPC module ID is **not** the same as the DAOS module ID. This is because a given DAOS module may need to register more than one dRPC module ID, depending on the functionality it covers. The dRPC module IDs must be unique system-wide and are listed in a central header file: `src/include/daos/drpc_modules.h`

The dRPC server uses the function `drpc_hdlr_process_msg` to handle incoming messages. This function checks the incoming message's module ID, searches for a handler, executes the handler if one is found, and returns the `Drpc__Response`. If none is found, it generates its own `Drpc__Response` indicating the module ID was not registered.


---

# Engine Scheduler (sched.c)

The engine scheduler's responsibility is to manage what is run on a pool target'
s xstream by deciding which ULT to run next and how many requests from the task
type to run.

- [ ] Question: targets can have multiple xstreams: the main xstream, offload
  xstreams
  and a metadata xstream. Is there a scheduler for each, or just one for the
  target??

## Goals

- Maintain I/O performance while progressing background tasks.
- Appropriately prioritize space reclamation activities if there is space
  pressure.

## Argobots

Uses argobots xstream, scheduler, ULTs, pools, units, etc

The entry point for the scheduler is when ABT_sched_create is called is.
Argobots will use the sched_init & sched_run callback functions.

There are 3 argobot pools for each xstream: Network polling, NVMe polling, and
all others (generic). See DSS_POOL_CNT

Note: It can get confusing thinking about DAOS pools and argobots pools.

- [ ] Could use more here on how argobots is used. I get lost on how work is
  actually continued. For example what does ABT_xstream_run_unit do compared to
  ABT_thread_resume. I also see a lot of popping of an ABT_unit which then gets
  run (ABT_xstream_run_unit), but I don't see any pushing onto the pool. How
  are network poll & NVMe tasks/ULTs added that get popped?

## Schedule Cycle

The scheduler runs in cycles.

- [ ] more info here

Prioritization/weights of the different ULTs is set at the beginning of the
cycle. For each cycle there is a number of requests that can be processed per
task type. (sched_req_info.req_limit). This limit is set by the throttling
logic.

- [ ] Provide examples of how that throttling logic works

## Task/ULT Types

There are currently 5 different types of tasks that are scheduled. Each of these
tasks runs in its own ULT.

- [ ] Question: Is this right -> "IO tasks are short lived ULTs? while
  background tasks are long running ULTs??"

1. IO Updates
2. IO Fetches
3. Background garbage collection
    - GC: 1 ULT per pool target
    - Aggregation: 1 ULT per container
4. Background checksum scrubbing (1 ULT per pool target)
5. Background data migration (rebuild)

## Prioritization & Space Pressure

The scheduler will prioritize IO tasks over background tasks, unless
there is space pressure, then garbage collection tasks (aggregation and GC) are
prioritized to attempt to free up space. The prioritization is based on how much
space pressure there is. The more space pressure, the more aggressive (higher
priority) garbage collection is.

## Understanding CPU Utilization

Each ULT will take a certain amount of CPU resources before it yields back to
the scheduler. The CPU cycles are not measured by the scheduler. Instead
estimated weights are used (req_weights[]) for each task type. For example,
UPDATE has a weight of 2 and FETCH has a weight of 1, meaning that it's
estimated that a FETCH will take twice the CPU resources as an UPDATE.

### Kick (Kicking off)

"Kick" is the term used in the code for what task to do (start/resume) next.
Call to ABT_thread_resume.

#### sched_req_wakeup

An external API to be used from one ULT to "wakeup" another ULT.

## Scheduling

- Each task/ULT has a hardcoded max milliseconds it can be delayed
- Each task/ULT has a hardcoded max QD (queue depth??) it can be delayed. When
  the number of requests reaches the QD max, then the task will be prioritized.

### Request Policy

- The default (only used right now) policy is FIFO. All requests for various
  pools are processed in FIFO order.
    - [ ] Question: Is this within a single task type or over all types. Meaning if these
  requests exist: UPDATE_1, GC_1, UPDATE_2, GC_2, are they run in that order or
  UPDATE_1, UPDATE_2, GC_2, GC_1 

## Environment Variables that impact the scheduler
- [ ] Not sure what all these are actually used for
- **DAOS_SCHED_PRIO_DISABLED** - disables scheduling prioritization 
- **DAOS_SCHED_RELAX_MODE** - ??
- **DAOS_SCHED_RELAX_INTVL** - ??
- **DAOS_SCHED_UNIT_RUNTIME_MAX** - ??
- **DAOS_SCHED_WATCHDOG_ALL** - ??
