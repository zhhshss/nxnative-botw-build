#ifndef NXK_STALL_POLICY_H
#define NXK_STALL_POLICY_H
#include <stdbool.h>
#include <stdint.h>
#include <math.h>
#include <string.h>

#define NXK_STALL_SESSION_SECONDS 60.0
#define NXK_STALL_WARMUP_SECONDS 2.0
#define NXK_STALL_THRESHOLD_SECONDS 0.1
#define NXK_STALL_COOLDOWN_SECONDS 2.0
#define NXK_STALL_CAPTURE_LIMIT 8u

typedef enum {
    NXK_STALL_STOP_NONE,
    NXK_STALL_STOP_CANCELLED,
    NXK_STALL_STOP_INELIGIBLE,
    NXK_STALL_STOP_EXPIRED,
    NXK_STALL_STOP_LIMIT,
    NXK_STALL_STOP_CLOCK
} NXKStallStopReason;

typedef struct {
    bool active,initialized,progress_seen,frame_captured;
    unsigned capture_count;
    uint64_t frame;
    double started_at,deadline,last_observation,last_progress,warmup_until,next_capture_at;
    NXKStallStopReason stop_reason;
} NXKStallPolicy;

/* The owner serializes calls. A zero-initialized policy is disabled. */
static inline void nxk_stall_cancel(NXKStallPolicy *p) {
    p->active=false;
    p->stop_reason=NXK_STALL_STOP_CANCELLED;
}

static inline bool nxk_stall_begin(NXKStallPolicy *p,double now) {
    memset(p,0,sizeof(*p));
    double deadline=now+NXK_STALL_SESSION_SECONDS;
    if (!isfinite(now) || !isfinite(deadline) || deadline<=now) {
        p->stop_reason=NXK_STALL_STOP_CLOCK;
        return false;
    }
    p->active=true;
    p->started_at=p->last_observation=now;
    p->deadline=deadline;
    return true;
}

static inline void nxk_stall_track(NXKStallPolicy *p,double now,uint64_t frame) {
    p->initialized=true;
    p->progress_seen=false;
    p->frame_captured=false;
    p->frame=frame;
    p->last_progress=now;
    p->warmup_until=now+NXK_STALL_WARMUP_SECONDS;
}

/* eligible must include foreground gameplay, closed settings and a valid,
 * unchanged target identity. Losing eligibility ends the session; resuming
 * requires an explicit begin. Counter rollback only restarts tracking, without
 * extending the original deadline or resetting the capture budget/cooldown.
 * A true return consumes one capture slot, even if collection later fails.
 * The eighth true return also marks the session inactive. */
static inline bool nxk_stall_observe(NXKStallPolicy *p,double now,uint64_t frame,bool eligible) {
    if (!p->active) return false;
    if (!isfinite(now) || now<p->last_observation) {
        p->active=false;
        p->stop_reason=NXK_STALL_STOP_CLOCK;
        return false;
    }
    p->last_observation=now;
    if (!eligible) {
        p->active=false;
        p->stop_reason=NXK_STALL_STOP_INELIGIBLE;
        return false;
    }
    if (now>=p->deadline) {
        p->active=false;
        p->stop_reason=NXK_STALL_STOP_EXPIRED;
        return false;
    }
    if (!p->initialized || frame<p->frame) {
        nxk_stall_track(p,now,frame);
        return false;
    }
    if (frame>p->frame) {
        p->frame=frame;
        p->progress_seen=true;
        p->frame_captured=false;
        p->last_progress=now;
        return false;
    }
    if (!p->progress_seen || p->frame_captured || now<p->warmup_until ||
        now<p->next_capture_at || now<p->last_progress+NXK_STALL_THRESHOLD_SECONDS) return false;
    p->frame_captured=true;
    p->next_capture_at=now+NXK_STALL_COOLDOWN_SECONDS;
    if (++p->capture_count==NXK_STALL_CAPTURE_LIMIT) {
        p->active=false;
        p->stop_reason=NXK_STALL_STOP_LIMIT;
    }
    return true;
}
#endif
