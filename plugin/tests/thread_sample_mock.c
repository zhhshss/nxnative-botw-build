/* Simulated Mach responses exercise the actual capture header control flow.
 * No OS threads are suspended or sampled by this host-side test. */

#include <assert.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <stdio.h>
typedef int kern_return_t; typedef unsigned thread_t, task_t, mach_msg_type_number_t; typedef thread_t *thread_act_array_t; typedef int *thread_info_t, *thread_state_t; typedef uintptr_t vm_address_t, vm_size_t;
typedef struct { int seconds, microseconds; } time_value_t;
typedef struct { time_value_t user_time, system_time; int cpu_usage, run_state, flags; } thread_basic_info_data_t;
typedef struct { uint64_t thread_id; } thread_identifier_info_data_t;
typedef struct { char pth_name[64]; } thread_extended_info_data_t;
typedef struct { uint64_t pc, lr, sp, fp; } arm_thread_state64_t;
typedef struct { uint32_t numer, denom; } mach_timebase_info_data_t;
#define KERN_SUCCESS 0
#define KERN_FAILURE 5
#define THREAD_IDENTIFIER_INFO 4
#define THREAD_IDENTIFIER_INFO_COUNT 2
#define THREAD_BASIC_INFO 3
#define THREAD_BASIC_INFO_COUNT 8
#define THREAD_EXTENDED_INFO 5
#define THREAD_EXTENDED_INFO_COUNT 16
#define ARM_THREAD_STATE64 6
#define ARM_THREAD_STATE64_COUNT 8
#define TH_FLAGS_IDLE 2
#define TH_STATE_RUNNING 1
#define arm_thread_state64_get_pc(s) ((s).pc)
#define arm_thread_state64_get_lr(s) ((s).lr)
#define arm_thread_state64_get_sp(s) ((s).sp)
#define arm_thread_state64_get_fp(s) ((s).fp)
static unsigned total, state_calls[512], deallocated[512], fail_state, short_state, fail_basic, fail_identifier, fail_enumeration;
static unsigned usage[512], run[512]; static thread_t ports[512]; static uint64_t tick;
static task_t mach_task_self(void) { return 1; }
static thread_t mach_thread_self(void) { return 1; }
static uint64_t mach_absolute_time(void) { tick+=1000; return tick; }
static kern_return_t mach_timebase_info(mach_timebase_info_data_t *t) { t->numer=t->denom=1; return 0; }
static kern_return_t thread_info(thread_t p,int flavor,thread_info_t out,mach_msg_type_number_t *n) {
 if(flavor==THREAD_BASIC_INFO) { if(p==fail_basic)return 5;thread_basic_info_data_t *v=(void*)out; *v=(thread_basic_info_data_t){.cpu_usage=(int)usage[p],.run_state=(int)run[p],.user_time={2,15}}; *n=THREAD_BASIC_INFO_COUNT; }
 if(flavor==THREAD_IDENTIFIER_INFO) { if(p==fail_identifier)return 5;((thread_identifier_info_data_t*)out)->thread_id=p+1000; *n=THREAD_IDENTIFIER_INFO_COUNT; }
 if(flavor==THREAD_EXTENDED_INFO) { snprintf(((thread_extended_info_data_t*)out)->pth_name,64,"thread%u",p); *n=THREAD_EXTENDED_INFO_COUNT; }
 return 0;
}
static kern_return_t task_threads(task_t t,thread_act_array_t *p,mach_msg_type_number_t *n) { assert(t==1);if(fail_enumeration)return 5;for(unsigned i=0;i<total;i++)ports[i]=i+1;*p=ports;*n=total;return 0; }
static kern_return_t thread_get_state(thread_t p,int f,thread_state_t out,mach_msg_type_number_t *n) {
 assert(f==ARM_THREAD_STATE64&&p!=1);state_calls[p]++;if(p==fail_state)return 5;
 *(arm_thread_state64_t*)out=(arm_thread_state64_t){.pc=0x200000+p*4,.lr=0x300000+p*4,.sp=0x100000,.fp=0x100100};*n=p==short_state?ARM_THREAD_STATE64_COUNT-1:ARM_THREAD_STATE64_COUNT;return 0;
}
static kern_return_t mach_port_deallocate(task_t t,thread_t p) { assert(t==1);deallocated[p]++;return 0; }
static kern_return_t vm_deallocate(task_t t,vm_address_t p,vm_size_t n) { assert(t==1&&p==(uintptr_t)ports&&n==total*sizeof(thread_t));return 0; }

#include "NXKThreadSample-under-test.h"

static void reset(void) {
 total=12;memset(state_calls,0,sizeof(state_calls));memset(deallocated,0,sizeof(deallocated));fail_state=short_state=fail_basic=fail_identifier=fail_enumeration=0;tick=0;
 for(unsigned p=0;p<512;p++){usage[p]=1000-p;run[p]=1;}
}
static void cleaned(void) { assert(deallocated[1]==2);for(unsigned p=2;p<=total;p++)assert(deallocated[p]==1); }
int main(void) {
 NXKThreadSnapshot s;
 reset();nxk_threads_capture(&s);assert(s.selected_count==8&&s.captured_count==8&&s.skipped_self==1);assert(s.threads[0].thread_id==1002&&s.threads[7].thread_id==1009);assert(s.threads[0].has_state&&s.threads[0].pc==0x200008&&s.threads[0].state_elapsed_ns==1000);for(unsigned p=2;p<=9;p++)assert(state_calls[p]==1);cleaned();
 reset();usage[12]=2000;nxk_threads_capture(&s);assert(s.threads[0].thread_id==1012&&s.threads[7].thread_id==1008&&state_calls[9]==0);cleaned();
 reset();fail_state=2;short_state=3;nxk_threads_capture(&s);assert(s.threads[0].state_error==5&&s.threads[0].state_attempted&&!s.threads[0].has_state);assert(s.threads[1].state_error==5&&!s.threads[1].has_state);assert(s.captured_count==6);cleaned();
 reset();fail_basic=2;nxk_threads_capture(&s);assert(s.info_failures==1&&s.threads[0].thread_id==1003&&state_calls[2]==0&&s.captured_count==8);cleaned();
 reset();fail_identifier=2;nxk_threads_capture(&s);assert(s.threads[0].identifier_error==5&&s.threads[0].thread_id==0&&s.threads[0].has_state);cleaned();
 reset();total=257;nxk_threads_capture(&s);assert(s.scan_limit_hit&&s.scanned_threads==0&&s.selected_count==0&&s.captured_count==0);cleaned();
 reset();fail_enumeration=1;nxk_threads_capture(&s);assert(s.enumeration_error==5&&s.selected_count==0&&deallocated[1]==1);
 reset();fail_identifier=1;nxk_threads_capture(&s);assert(s.preflight_error==5&&s.selected_count==0&&deallocated[1]==1);
 puts("PASS: CPU selection/self exclusion; failed or short state reads; metadata errors; all send-right cleanup; scan cap; enumeration and preflight failures. No user suspend/resume or stack VM reads.");
}
