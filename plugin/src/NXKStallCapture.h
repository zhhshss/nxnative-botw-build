#ifndef NXK_STALL_CAPTURE_H
#define NXK_STALL_CAPTURE_H
#import <Foundation/Foundation.h>
#import <QuartzCore/QuartzCore.h>
#import <dlfcn.h>
#include <stdatomic.h>
#include "NXKRenderProbe.h"
#include "NXKStallPolicy.h"
#include "NXKThreadSample.h"

static NSString *NXKAddress(uint64_t address) {
    return [NSString stringWithFormat:@"0x%llx",(unsigned long long)address];
}
static NSDictionary *NXKCodeAddress(uint64_t address) {
    NSMutableDictionary *result=[@{@"address":NXKAddress(address)} mutableCopy];
    /* Saved return addresses can carry PAC bits. Keep the original as evidence. */
    uintptr_t lookup=(uintptr_t)(address & UINT64_C(0x0000ffffffffffff));
    if (lookup!=(uintptr_t)address) result[@"lookupAddress"]=NXKAddress(lookup);
    Dl_info info={0};
    if (lookup && dladdr((const void *)lookup,&info)) {
        if (info.dli_fname) result[@"image"]=[@(info.dli_fname) lastPathComponent];
        result[@"imageBase"]=NXKAddress((uintptr_t)info.dli_fbase);
        result[@"imageOffset"]=NXKAddress(lookup-(uintptr_t)info.dli_fbase);
        if (info.dli_sname && info.dli_saddr) {
            result[@"symbol"]=@(info.dli_sname);
            result[@"symbolOffset"]=NXKAddress(lookup-(uintptr_t)info.dli_saddr);
        }
    }
    return result;
}

/* Kernel-managed register snapshots complete before any formatting. No
 * user-space suspend count or frame-pointer stack walk is used. */
static NSDictionary *NXKThreadSnapshotJSON(const NXKThreadSnapshot *snapshot) {
    NSMutableArray *threads=[NSMutableArray array];
    for (unsigned i=0;i<snapshot->selected_count && i<NXK_THREAD_SAMPLE_CAP;i++) {
        const NXKThreadRecord *r=&snapshot->threads[i];
        NSString *name=[[NSString alloc] initWithBytes:r->name length:strnlen(r->name,sizeof(r->name)) encoding:NSUTF8StringEncoding]?:@"unknown";
        NSMutableDictionary *row=[@{@"id":@(r->thread_id),@"name":name,@"cpuUsageEstimatePercent":@(r->cpu_usage*100.0/TH_USAGE_SCALE),@"cpuTotalMs":@(r->cpu_total_us/1000.0),@"runState":@(r->run_state),@"identifierError":@(r->identifier_error),@"nameError":@(r->name_error),@"stateError":@(r->state_error),@"stateAttempted":@(r->state_attempted),@"hasState":@(r->has_state),@"stateReadMs":@(r->state_elapsed_ns/1e6)} mutableCopy];
        if (r->has_state) {
            row[@"pc"]=NXKCodeAddress(r->pc);
            row[@"lr"]=NXKCodeAddress(r->lr);
            row[@"sp"]=NXKAddress(r->sp);
            row[@"fp"]=NXKAddress(r->fp);
        }
        [threads addObject:row];
    }
    return @{@"method":@"kernel-managed register snapshots; no stack walk",@"enumerationError":@(snapshot->enumeration_error),@"preflightError":@(snapshot->preflight_error),@"cleanupError":@(snapshot->cleanup_error),@"totalThreads":@(snapshot->total_threads),@"scannedThreads":@(snapshot->scanned_threads),@"selectedThreads":@(snapshot->selected_count),@"capturedThreads":@(snapshot->captured_count),@"infoFailures":@(snapshot->info_failures),@"skippedSelf":@(snapshot->skipped_self),@"scanLimitHit":@(snapshot->scan_limit_hit),@"elapsedMs":@(snapshot->elapsed_ns/1e6),@"records":threads};
}

@interface NXKStallCapture : NSObject {
    _Atomic uintptr_t *_liveImage;
    _Atomic uint64_t *_imageEpoch;
    _Atomic unsigned _generation;
    _Atomic uint64_t _sessionID;
    _Atomic bool _eligible;
}
@property(nonatomic,strong) dispatch_source_t timer;
@property(nonatomic,strong) dispatch_queue_t queue;
@property(nonatomic,strong) NSMutableArray<NSDictionary *> *samples;
@property(nonatomic,strong) NSString *status;
- (instancetype)initWithLiveImage:(_Atomic uintptr_t *)liveImage epoch:(_Atomic uint64_t *)imageEpoch;
- (void)setEligible:(BOOL)eligible;
- (void)beginWithBase:(uintptr_t)base;
- (void)stopWithReason:(NSString *)reason;
- (NSDictionary *)report;
@end

@implementation NXKStallCapture
- (instancetype)initWithLiveImage:(_Atomic uintptr_t *)liveImage epoch:(_Atomic uint64_t *)imageEpoch {
    if ((self=[super init])) {
        _liveImage=liveImage;
        _imageEpoch=imageEpoch;
        self.samples=[NSMutableArray array];
        self.status=@"未开启";
        self.queue=dispatch_queue_create("org.nxkeyboard.stall-sample",dispatch_queue_attr_make_with_qos_class(DISPATCH_QUEUE_SERIAL,QOS_CLASS_UTILITY,0));
    }
    return self;
}
/* All public methods and stored reports are main-thread owned. */
- (void)setEligible:(BOOL)eligible {
    atomic_store(&_eligible,eligible);
    if (!eligible && self.timer) [self stopWithReason:@"已停止（游戏不在前台或设置已打开）"];
}
- (void)stopWithReason:(NSString *)reason {
    if (!self.timer) return;
    atomic_fetch_add(&_generation,1);
    dispatch_source_cancel(self.timer);
    self.timer=nil;
    self.status=reason;
    NSLog(@"[NXKeyboard][stall] %@; records=%lu",reason,(unsigned long)self.samples.count);
}
- (void)beginWithBase:(uintptr_t)base {
    [self stopWithReason:@"已重新开始"];
    uint64_t imageEpoch=atomic_load(_imageEpoch);
    if (!base || atomic_load(_liveImage)!=base || !atomic_load(&_eligible) || atomic_load(_imageEpoch)!=imageEpoch) {
        self.status=@"无法开始：游戏版本或前台状态不匹配";
        return;
    }
    [self.samples removeAllObjects];
    self.status=@"采集中（最多 60 秒）";
    unsigned generation=atomic_fetch_add(&_generation,1)+1;
    uint64_t session=atomic_fetch_add(&_sessionID,1)+1;
    __block NXKStallPolicy policy={0};
    __block BOOL completionSent=NO;
    nxk_stall_begin(&policy,CACurrentMediaTime());
    self.timer=dispatch_source_create(DISPATCH_SOURCE_TYPE_TIMER,0,0,self.queue);
    dispatch_source_set_timer(self.timer,dispatch_time(DISPATCH_TIME_NOW,0),20*NSEC_PER_MSEC,5*NSEC_PER_MSEC);
    __weak NXKStallCapture *weakSelf=self;
    dispatch_source_set_event_handler(self.timer,^{
        if (completionSent) return;
        NXKStallCapture *s=weakSelf;
        if (!s || atomic_load(&s->_generation)!=generation) return;
        @autoreleasepool {
            double now=CACurrentMediaTime();
            BOOL eligible=atomic_load(&s->_eligible) && atomic_load(s->_liveImage)==base && atomic_load(s->_imageEpoch)==imageEpoch;
            uint64_t frame=0; vm_size_t bytes=0;
            kern_return_t read=KERN_INVALID_ADDRESS;
            if (eligible) read=vm_read_overwrite(mach_task_self(),(vm_address_t)(base+NXK_RENDER_FRAME_OFFSET),sizeof(frame),(vm_address_t)&frame,&bytes);
            eligible=eligible && read==KERN_SUCCESS && bytes==sizeof(frame) && atomic_load(s->_liveImage)==base && atomic_load(s->_imageEpoch)==imageEpoch && atomic_load(&s->_generation)==generation && atomic_load(&s->_eligible);
            BOOL capture=nxk_stall_observe(&policy,now,frame,eligible);
            if (capture) {
                if (atomic_load(&s->_generation)!=generation || !atomic_load(&s->_eligible) || atomic_load(s->_liveImage)!=base || atomic_load(s->_imageEpoch)!=imageEpoch) return;
                double gap=(now-policy.last_progress)*1000.0;
                NXKThreadSnapshot snapshot;
                double started=CACurrentMediaTime();
                nxk_threads_capture(&snapshot);
                double finished=CACurrentMediaTime();
                double captureMs=(finished-started)*1000.0;
                policy.next_capture_at=fmax(policy.next_capture_at,finished+NXK_STALL_COOLDOWN_SECONDS);
                if (atomic_load(&s->_generation)!=generation || !atomic_load(&s->_eligible) || atomic_load(s->_liveImage)!=base || atomic_load(s->_imageEpoch)!=imageEpoch) return;
                NSMutableDictionary *entry=[@{@"session":@(session),@"index":@(policy.capture_count),@"time":@(now),@"frame":@(frame),@"observedNoFrameProgressMs":@(gap),@"captureCostMs":@(captureMs),@"hostImageBase":NXKAddress(base),@"hostImageEpoch":@(imageEpoch),@"threads":NXKThreadSnapshotJSON(&snapshot)} mutableCopy];
                uint64_t after=0; vm_size_t afterBytes=0;
                if (atomic_load(s->_liveImage)==base && atomic_load(s->_imageEpoch)==imageEpoch && vm_read_overwrite(mach_task_self(),(vm_address_t)(base+NXK_RENDER_FRAME_OFFSET),sizeof(after),(vm_address_t)&after,&afterBytes)==KERN_SUCCESS && afterBytes==sizeof(after)) entry[@"frameAfterCapture"]=@(after);
                NSData *data=[NSJSONSerialization dataWithJSONObject:entry options:0 error:nil];
                if (atomic_load(&s->_generation)!=generation || !atomic_load(&s->_eligible) || atomic_load(s->_liveImage)!=base || atomic_load(s->_imageEpoch)!=imageEpoch) return;
                if (data) NSLog(@"[NXKeyboard][stall] %@",[[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding]);
                dispatch_async(dispatch_get_main_queue(),^{
                    /* A normal stop retains completed records; a new session
                     * rejects old records that were still queued for the UI. */
                    if (atomic_load(&s->_sessionID)==session) [s.samples addObject:entry];
                });
            }
            if (!policy.active) {
                completionSent=YES;
                unsigned captured=policy.capture_count;
                dispatch_async(dispatch_get_main_queue(),^{
                    if (atomic_load(&s->_generation)==generation)
                        [s stopWithReason:eligible?(captured?@"采集已结束":@"采集已结束，未捕获超过 100 ms 的停顿"):@"已停止（游戏状态改变或计数不可读）"];
                });
            }
        }
    });
    NSLog(@"[NXKeyboard][stall] sampling started; duration=60s threshold=100ms limit=8 host=%@",NXKAddress(base));
    dispatch_resume(self.timer);
}
- (NSDictionary *)report {
    return @{@"status":self.status,@"session":@(atomic_load(&_sessionID)),@"durationSeconds":@60,@"thresholdMs":@100,@"maximumSamples":@8,@"samples":[self.samples copy]};
}
@end
#endif
