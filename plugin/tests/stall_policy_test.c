#include <assert.h>
#include <float.h>
#include <math.h>
#include <stdio.h>
#include "../src/NXKStallPolicy.h"

static void warm(NXKStallPolicy *p,double start,uint64_t frame) {
    assert(nxk_stall_begin(p,start));
    assert(!nxk_stall_observe(p,start,frame,true));
    assert(!nxk_stall_observe(p,start+1.0,frame+1,true));
    assert(!nxk_stall_observe(p,start+1.999,frame+1,true));
}

static void test_disabled_and_warmup(void) {
    NXKStallPolicy p={0};
    assert(!nxk_stall_observe(&p,100,10,true));
    assert(!p.active && p.capture_count==0);
    assert(nxk_stall_begin(&p,100));
    assert(!nxk_stall_observe(&p,100,10,true));
    assert(!nxk_stall_observe(&p,105,10,true)); /* No progress: not armed for a hitch. */
    assert(!nxk_stall_observe(&p,105,11,true));
    assert(!nxk_stall_observe(&p,105.099,11,true));
    assert(nxk_stall_observe(&p,105+NXK_STALL_THRESHOLD_SECONDS,11,true));
    assert(p.capture_count==1 && p.last_progress==105);

    warm(&p,200,20);
    assert(nxk_stall_observe(&p,202,21,true));
}

static void test_progress_and_same_frame_once(void) {
    NXKStallPolicy p;
    warm(&p,0,100);
    assert(!nxk_stall_observe(&p,2,102,true));
    assert(!nxk_stall_observe(&p,2.05,103,true));
    assert(!nxk_stall_observe(&p,2.10,104,true));
    assert(!nxk_stall_observe(&p,2.199,104,true));
    assert(nxk_stall_observe(&p,2.10+NXK_STALL_THRESHOLD_SECONDS,104,true));
    assert(!nxk_stall_observe(&p,3,104,true));
    assert(!nxk_stall_observe(&p,5,104,true)); /* Cooldown alone does not recapture a frozen frame. */
    assert(p.capture_count==1);
}

static void test_cooldown_and_limit(void) {
    NXKStallPolicy p;
    warm(&p,0,100);
    assert(nxk_stall_observe(&p,2,101,true));
    assert(!nxk_stall_observe(&p,2.2,102,true));
    assert(!nxk_stall_observe(&p,3.999,102,true));
    assert(nxk_stall_observe(&p,4,102,true));
    for (unsigned n=3;n<=NXK_STALL_CAPTURE_LIMIT;n++) {
        double capture=(double)n*2;
        assert(!nxk_stall_observe(&p,capture-0.5,100+n,true));
        assert(nxk_stall_observe(&p,capture,100+n,true));
        assert(p.capture_count==n);
    }
    assert(!p.active && p.stop_reason==NXK_STALL_STOP_LIMIT);
    assert(!nxk_stall_observe(&p,20,999,true));
    assert(!nxk_stall_observe(&p,21,999,true));
    assert(p.capture_count==NXK_STALL_CAPTURE_LIMIT);
}

static void test_cancel_and_ineligible(void) {
    NXKStallPolicy p;
    warm(&p,0,100);
    nxk_stall_cancel(&p);
    assert(!p.active && p.stop_reason==NXK_STALL_STOP_CANCELLED);
    assert(!nxk_stall_observe(&p,2,101,true));
    warm(&p,10,200);
    assert(!nxk_stall_observe(&p,12,201,false)); /* Settings, background, or target change. */
    assert(!p.active && p.stop_reason==NXK_STALL_STOP_INELIGIBLE);
    assert(!nxk_stall_observe(&p,13,201,true)); /* No automatic restart. */
    assert(nxk_stall_begin(&p,20));
    assert(p.active && p.capture_count==0 && p.stop_reason==NXK_STALL_STOP_NONE);
    assert(!nxk_stall_observe(&p,20,300,true));
    assert(!nxk_stall_observe(&p,21,301,true));
    assert(nxk_stall_observe(&p,22,301,true));
}

static void test_expiry(void) {
    NXKStallPolicy p;
    assert(nxk_stall_begin(&p,10));
    assert(!nxk_stall_observe(&p,69.8,1,true)); /* A late first observation cannot extend the session. */
    assert(!nxk_stall_observe(&p,69.9,2,true));
    assert(!nxk_stall_observe(&p,70,2,true));
    assert(!p.active && p.stop_reason==NXK_STALL_STOP_EXPIRED);
    warm(&p,100,100);
    assert(nxk_stall_observe(&p,102,101,true));
    assert(!nxk_stall_observe(&p,160,101,true));
    assert(!p.active && p.capture_count==1 && p.stop_reason==NXK_STALL_STOP_EXPIRED);
}

static void test_clock_and_counter_resets(void) {
    NXKStallPolicy p;
    assert(!nxk_stall_begin(&p,NAN));
    assert(!p.active && p.stop_reason==NXK_STALL_STOP_CLOCK);
    assert(!nxk_stall_begin(&p,INFINITY));
    assert(!nxk_stall_begin(&p,DBL_MAX)); /* The deadline must be representably later. */
    warm(&p,10,100);
    assert(!nxk_stall_observe(&p,NAN,101,true));
    assert(!p.active && p.stop_reason==NXK_STALL_STOP_CLOCK);
    warm(&p,20,100);
    assert(!nxk_stall_observe(&p,20.5,101,true)); /* Earlier than the last observation. */
    assert(!p.active && p.stop_reason==NXK_STALL_STOP_CLOCK);

    warm(&p,0,100);
    assert(nxk_stall_observe(&p,2,101,true));
    assert(!nxk_stall_observe(&p,2.1,5,true)); /* Counter reset starts a fresh warmup. */
    assert(p.active && p.capture_count==1 && p.deadline==60);
    assert(!nxk_stall_observe(&p,4.1,5,true)); /* A new epoch still requires progress. */
    assert(!nxk_stall_observe(&p,4.2,6,true));
    assert(nxk_stall_observe(&p,4.2+NXK_STALL_THRESHOLD_SECONDS,6,true));
    assert(p.capture_count==2);
    assert(!nxk_stall_observe(&p,59,1,true));
    assert(!nxk_stall_observe(&p,59.1,2,true));
    assert(!nxk_stall_observe(&p,60,2,true));
    assert(!p.active && p.stop_reason==NXK_STALL_STOP_EXPIRED);
}

int main(void) {
    test_disabled_and_warmup();
    test_progress_and_same_frame_once();
    test_cooldown_and_limit();
    test_cancel_and_ineligible();
    test_expiry();
    test_clock_and_counter_resets();
    puts("Stall policy checks passed: disabled default, progress/warmup, threshold, same-frame suppression, cooldown, capture cap, cancellation, expiry, clock and counter resets.");
    return 0;
}
