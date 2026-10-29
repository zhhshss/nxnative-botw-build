#ifndef NXK_THREAD_SAMPLE_H
#define NXK_THREAD_SAMPLE_H
#include <mach/mach.h>
#include <mach/mach_time.h>
#include <mach/arm/thread_status.h>
#include <mach/vm_map.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

/* One synchronous snapshot; call on one dedicated diagnostic queue.
 * CPU usage is Mach's decaying estimate, not an instantaneous measurement.
 * thread_get_state obtains each register snapshot through the kernel, which
 * manages its own temporary hold/stop/release. No user-owned suspension or
 * stack-memory walk is performed. The registers are not simultaneous across
 * threads and a kernel call has no hard wall-clock time bound.
 * Releasing returned send rights remains proportional to task_threads count. */
#define NXK_THREAD_SAMPLE_CAP 8u
#define NXK_THREAD_SCAN_CAP 256u
#define NXK_THREAD_NOT_ATTEMPTED ((kern_return_t)-1)

typedef struct {
    uint64_t thread_id, cpu_total_us;
    int cpu_usage, run_state;
    char name[64];
    kern_return_t identifier_error, name_error;
    kern_return_t state_error;
    bool state_attempted, has_state;
    uint64_t pc, lr, sp, fp, state_elapsed_ns;
} NXKThreadRecord;

typedef struct {
    kern_return_t enumeration_error, preflight_error, cleanup_error;
    unsigned total_threads, scanned_threads, selected_count, captured_count;
    unsigned info_failures, skipped_self;
    bool scan_limit_hit;
    uint64_t elapsed_ns;
    NXKThreadRecord threads[NXK_THREAD_SAMPLE_CAP];
} NXKThreadSnapshot;

static inline uint64_t nxk_thread_time_us(time_value_t t) {
    return t.seconds >= 0 && t.microseconds >= 0 ?
        (uint64_t)t.seconds * 1000000 + (uint64_t)t.microseconds : 0;
}

static inline uint64_t nxk_thread_ticks_ns(uint64_t ticks, mach_timebase_info_data_t tb) {
    return (uint64_t)((long double)ticks * tb.numer / tb.denom);
}

static inline void nxk_threads_capture(NXKThreadSnapshot *out) {
    if (!out) return;
    memset(out, 0, sizeof(*out));
    out->enumeration_error = out->preflight_error = NXK_THREAD_NOT_ATTEMPTED;
    mach_timebase_info_data_t tb = {0};
    out->preflight_error = mach_timebase_info(&tb);
    if (out->preflight_error != KERN_SUCCESS || !tb.numer || !tb.denom) return;
    uint64_t started = mach_absolute_time();
    task_t task = mach_task_self();
    thread_t self = mach_thread_self();
    thread_identifier_info_data_t self_id = {0};
    mach_msg_type_number_t n = THREAD_IDENTIFIER_INFO_COUNT;
    out->preflight_error = thread_info(self, THREAD_IDENTIFIER_INFO,
        (thread_info_t)&self_id, &n);
    if (out->preflight_error != KERN_SUCCESS || n != THREAD_IDENTIFIER_INFO_COUNT) {
        if (out->preflight_error == KERN_SUCCESS) out->preflight_error = KERN_FAILURE;
        mach_port_deallocate(task, self);
        return;
    }

    thread_act_array_t ports = NULL;
    mach_msg_type_number_t count = 0;
    out->enumeration_error = task_threads(task, &ports, &count);
    if (out->enumeration_error != KERN_SUCCESS) {
        mach_port_deallocate(task, self);
        return;
    }
    out->total_threads = count;
    struct { thread_t port; thread_basic_info_data_t basic; } chosen[NXK_THREAD_SAMPLE_CAP];
    memset(chosen, 0, sizeof(chosen));
    /* Do not silently choose only the first part of an unexpectedly large task.
     * Even on this path all task_threads send rights are released below. */
    if (count > NXK_THREAD_SCAN_CAP) out->scan_limit_hit = true;
    else for (unsigned i = 0; i < count; i++) {
        if (ports[i] == self) { out->skipped_self++; continue; }
        thread_basic_info_data_t basic = {0};
        n = THREAD_BASIC_INFO_COUNT;
        kern_return_t kr = thread_info(ports[i], THREAD_BASIC_INFO, (thread_info_t)&basic, &n);
        out->scanned_threads++;
        if (kr != KERN_SUCCESS || n != THREAD_BASIC_INFO_COUNT) { out->info_failures++; continue; }
        if (basic.flags & TH_FLAGS_IDLE) continue;
        unsigned at = 0;
        for (; at < out->selected_count; at++) {
            thread_basic_info_data_t prev = chosen[at].basic;
            if (basic.cpu_usage > prev.cpu_usage ||
                (basic.cpu_usage == prev.cpu_usage && basic.run_state == TH_STATE_RUNNING &&
                 prev.run_state != TH_STATE_RUNNING)) break;
        }
        if (at == NXK_THREAD_SAMPLE_CAP) continue;
        if (out->selected_count < NXK_THREAD_SAMPLE_CAP) out->selected_count++;
        for (unsigned j = out->selected_count - 1; j > at; j--) chosen[j] = chosen[j - 1];
        chosen[at].port = ports[i]; chosen[at].basic = basic;
    }

    /* Finish metadata before reading registers; keep ranking and capture separate. */
    for (unsigned i = 0; i < out->selected_count; i++) {
        NXKThreadRecord *r = &out->threads[i];
        r->cpu_usage = chosen[i].basic.cpu_usage;
        r->run_state = chosen[i].basic.run_state;
        r->cpu_total_us = nxk_thread_time_us(chosen[i].basic.user_time) +
            nxk_thread_time_us(chosen[i].basic.system_time);
        r->state_error = NXK_THREAD_NOT_ATTEMPTED;
        thread_identifier_info_data_t id = {0};
        n = THREAD_IDENTIFIER_INFO_COUNT;
        r->identifier_error = thread_info(chosen[i].port, THREAD_IDENTIFIER_INFO, (thread_info_t)&id, &n);
        if (r->identifier_error == KERN_SUCCESS && n == THREAD_IDENTIFIER_INFO_COUNT) r->thread_id = id.thread_id;
        else if (r->identifier_error == KERN_SUCCESS) r->identifier_error = KERN_FAILURE;
        thread_extended_info_data_t ext = {0};
        n = THREAD_EXTENDED_INFO_COUNT;
        r->name_error = thread_info(chosen[i].port, THREAD_EXTENDED_INFO, (thread_info_t)&ext, &n);
        if (r->name_error == KERN_SUCCESS && n == THREAD_EXTENDED_INFO_COUNT) {
            memcpy(r->name, ext.pth_name, sizeof(r->name)); r->name[sizeof(r->name) - 1] = 0;
        } else if (r->name_error == KERN_SUCCESS) r->name_error = KERN_FAILURE;
    }

    for (unsigned i = 0; i < out->selected_count; i++) {
        NXKThreadRecord *r = &out->threads[i];
        if (r->thread_id == self_id.thread_id) { out->skipped_self++; continue; }
        arm_thread_state64_t state = {0};
        n = ARM_THREAD_STATE64_COUNT;
        r->state_attempted = true;
        uint64_t began = mach_absolute_time();
        r->state_error = thread_get_state(chosen[i].port, ARM_THREAD_STATE64, (thread_state_t)&state, &n);
        r->state_elapsed_ns = nxk_thread_ticks_ns(mach_absolute_time() - began, tb);
        if (r->state_error == KERN_SUCCESS && n == ARM_THREAD_STATE64_COUNT) {
            r->has_state = true;
            r->pc = arm_thread_state64_get_pc(state); r->lr = arm_thread_state64_get_lr(state);
            r->sp = arm_thread_state64_get_sp(state); r->fp = arm_thread_state64_get_fp(state);
            out->captured_count++;
        } else if (r->state_error == KERN_SUCCESS) r->state_error = KERN_FAILURE;
    }

    /* task_threads returns one send right per entry; self has its own extra ref. */
    for (unsigned i = 0; i < count; i++) {
        kern_return_t kr = mach_port_deallocate(task, ports[i]);
        if (kr != KERN_SUCCESS && out->cleanup_error == KERN_SUCCESS) out->cleanup_error = kr;
    }
    if (ports) {
        kern_return_t kr = vm_deallocate(task, (vm_address_t)ports, (vm_size_t)count * sizeof(thread_t));
        if (kr != KERN_SUCCESS && out->cleanup_error == KERN_SUCCESS) out->cleanup_error = kr;
    }
    kern_return_t kr = mach_port_deallocate(task, self);
    if (kr != KERN_SUCCESS && out->cleanup_error == KERN_SUCCESS) out->cleanup_error = kr;
    out->elapsed_ns = nxk_thread_ticks_ns(mach_absolute_time() - started, tb);
}
#endif
