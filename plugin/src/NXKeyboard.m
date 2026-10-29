#import <UIKit/UIKit.h>
#import <GameController/GameController.h>
#import <CommonCrypto/CommonDigest.h>
#import <mach-o/dyld.h>
#import <mach-o/loader.h>
#import <objc/runtime.h>
#include <math.h>
#include <stdlib.h>
#include "NXKInput.h"
#include "NXKTarget.h"
#include "NXKWriter.h"
#include "NXKMouse.h"
#include "NXKPointerPolicy.h"
#include "NXKRenderProbe.h"
#include "NXKStallCapture.h"
#include "NXKDisplay.h"
#include "NXKTouchView.h"
#include "NXKResourceExport.h"

static NSString *const NXKNames[NXK_ACTION_COUNT]={
    @"左摇杆 ←",@"左摇杆 →",@"左摇杆 ↑",@"左摇杆 ↓",
    @"右摇杆 ←（视角）",@"右摇杆 →（视角）",@"右摇杆 ↑（视角）",@"右摇杆 ↓（视角）",
    @"A · 确认 / 互动",@"B · 冲刺 / 取消",@"X · 跳跃",@"Y · 攻击",
    @"L3 · 蹲下",@"R3 · 望远镜",@"L · 符文",@"R · 投掷",
    @"ZL · 锁定 / 盾",@"ZR · 弓",@"+ · 菜单",@"− · 地图",
    @"十字键 ←",@"十字键 ↑",@"十字键 →",@"十字键 ↓",
    @"慢走（按住）",@"开关快捷键（可选）",@"鼠标游戏模式开关"
};
static _Atomic uintptr_t NXKLiveImage;
static _Atomic uint64_t NXKImageEpoch;
static void NXKImageRemoved(const struct mach_header *header,intptr_t slide) {
    (void)slide;
    uintptr_t expected=(uintptr_t)header;
    if (atomic_compare_exchange_strong(&NXKLiveImage,&expected,0)) atomic_fetch_add(&NXKImageEpoch,1);
}

@class NXKManager;
static __weak NXKManager *NXKActiveManager;
@interface NXKSettings : UITableViewController
@property(nonatomic,weak) NXKManager *manager;
@end
@interface NXKWindow : UIWindow
@end
@implementation NXKWindow
- (UIView *)hitTest:(CGPoint)p withEvent:(UIEvent *)event {
    UIView *v=[super hitTest:p withEvent:event];
    if (self.rootViewController.presentedViewController) return v;
    return v==self || v==self.rootViewController.view ? nil : v;
}
@end

@interface NXKManager : NSObject {
@public
    NXKInput input;
    NXKWriter writer;
    NXKMouseMotion mouseMotion;
    NXKPointerPolicy pointerPolicy;
    NXKRenderProbe renderProbe;
}
@property(nonatomic,strong) NSUserDefaults *prefs;
@property(nonatomic,strong) NXKWindow *overlay;
@property(nonatomic,strong) UIButton *button;
@property(nonatomic,strong) NSTimer *timer;
@property(nonatomic,strong) GCKeyboardInput *keyboard;
@property(nonatomic,copy) GCKeyboardValueChangedHandler previousHandler;
@property(nonatomic,copy) GCKeyboardValueChangedHandler ownHandler;
@property(nonatomic,strong) GCMouseInput *mouse;
@property(nonatomic,copy) GCMouseMoved previousMouseMoved, ownMouseMoved;
@property(nonatomic,copy) GCControllerDirectionPadValueChangedHandler previousScroll, ownScroll;
@property(nonatomic,strong) NSArray *mouseButtons, *previousMouseButtons, *ownMouseButtons;
@property(nonatomic) BOOL mouseLook, invertMouseY, lockPointer, mouseReleased, pointerRequested, gamePointerLocked;
@property(nonatomic) double mouseSensitivity, lastMouseTick;
@property(nonatomic) double inputTimerInterval;
@property(nonatomic,strong) NSHashTable<UIViewController *> *pointerRoots;
@property(nonatomic,strong) NSArray<UIViewController *> *pointerRootList;
@property(nonatomic) uint64_t pointerRootGeneration;
@property(nonatomic) double lastRootScan,lastContextScan,lastTouchScan,lastDeviceScan,lastDiagnostics,lastGameplayDiagnostics;
@property(nonatomic) BOOL textInputBlocked;
@property(nonatomic,strong) NSString *lastButtonTitle;
@property(nonatomic) CGRect lastOverlayBounds;
@property(nonatomic) UIEdgeInsets lastOverlayInsets;
@property(nonatomic,strong) NSMutableArray<NSDictionary *> *performanceSamples;
@property(nonatomic,strong) NSDictionary *lastGameplayPointer;
@property(nonatomic,strong) NXKStallCapture *stallCapture;
@property(nonatomic,strong) NXKResourceExporter *resourceExporter;
@property(nonatomic,strong) UIAlertController *captureAlert;
@property(nonatomic,weak) NXKSettings *settings;
@property(nonatomic,weak) UIView *touchPad;
@property(nonatomic,weak) CALayer *touchControls;
@property(nonatomic) BOOL hideTouchControls, controlsHiddenByPlugin;
@property(nonatomic,strong) NXKDisplayController *display;
@property(nonatomic,strong) NXKTouchView *touchView;
@property(nonatomic) BOOL enhancedTouch,touchIntegrationReady,nativeTouchTaken,displayHooksReady;
@property(nonatomic) CGFloat touchScale,touchSensitivity;
@property(nonatomic) BOOL invertTouchY;
@property(nonatomic) double lastDisplayScan;
@property(nonatomic) NXKOutput lastPublishedOutput;
@property(nonatomic) NSInteger captureAction;
@property(nonatomic) BOOL enabled, targetReady, ownsSlot, active, suspended, dragging;
@property(nonatomic) BOOL previousToggle;
@property(nonatomic) uintptr_t targetBase;
@property(nonatomic) CGPoint dragOrigin;
@property(nonatomic,strong) NSString *error;
- (void)start;
- (void)save;
- (void)refresh;
- (void)clear;
- (void)capture:(NSInteger)action from:(UIViewController *)vc;
- (void)setMappingEnabled:(BOOL)value;
- (NSString *)bindingLabel:(NSInteger)action;
- (void)finishCapture;
- (BOOL)wantsPointerLock;
- (BOOL)suppressPointerTouches;
- (BOOL)ownsPointerRoot:(UIViewController *)controller;
- (NSString *)diagnosticText;
- (void)publish;
- (BOOL)slotAvailable;
- (BOOL)gameIsForeground;
- (BOOL)swallowsNativeTouchFor:(UIView *)pad;
- (BOOL)pushMappedNativeTouches:(UIView *)pad;
- (void)updateTouchControls;
- (void)updateDisplay;
- (void)setEnhancedTouchEnabled:(BOOL)value;
- (void)resetNativeTouch;
- (void)sendDirectTouches:(const NXKGameTouch *)points count:(int)count;
@end

static void NXKHookPointerRootClass(Class cls) {
    static NSMutableSet<NSString *> *hooked;
    if (!hooked) hooked=[NSMutableSet set];
    NSString *name=NSStringFromClass(cls);
    if ([hooked containsObject:name]) return;
    [hooked addObject:name];
    SEL selector=@selector(prefersPointerLocked);
    Method method=class_getInstanceMethod(cls,selector);
    if (method) {
        IMP original=method_getImplementation(method);
        IMP replacement=imp_implementationWithBlock(^BOOL(UIViewController *object) {
            NXKManager *m=NXKActiveManager;
            if ([m ownsPointerRoot:object]) return [m wantsPointerLock];
            return ((BOOL(*)(id,SEL))original)(object,selector);
        });
        class_replaceMethod(cls,selector,replacement,method_getTypeEncoding(method));
    }
    SEL childSelector=@selector(childViewControllerForPointerLock);
    Method child=class_getInstanceMethod(cls,childSelector);
    if (child) {
        IMP original=method_getImplementation(child);
        IMP replacement=imp_implementationWithBlock(^UIViewController *(UIViewController *object) {
            if ([NXKActiveManager ownsPointerRoot:object]) return nil;
            return ((id(*)(id,SEL))original)(object,childSelector);
        });
        class_replaceMethod(cls,childSelector,replacement,method_getTypeEncoding(child));
    }
}
@interface NXKOverlayRoot : UIViewController
@end
@implementation NXKOverlayRoot
- (BOOL)prefersPointerLocked { return [NXKActiveManager wantsPointerLock]; }
- (UIViewController *)childViewControllerForPointerLock { return nil; }
- (BOOL)prefersStatusBarHidden { return !self.presentedViewController && NXKActiveManager.display.immersive; }
- (BOOL)prefersHomeIndicatorAutoHidden { return !self.presentedViewController && NXKActiveManager.display.immersive; }
- (UIRectEdge)preferredScreenEdgesDeferringSystemGestures { return self.presentedViewController || !NXKActiveManager.display.immersive?UIRectEdgeNone:UIRectEdgeAll; }
@end

static void NXKInstallPointerHooks(void) {
    static BOOL installed=NO;
    if (installed) return;
    Class pad=NSClassFromString(@"DmGamepad");
    if (!pad) return;
    for (NSString *name in @[@"touchesBegan:withEvent:",@"touchesMoved:withEvent:",@"touchesEnded:withEvent:",@"touchesCancelled:withEvent:"]) {
        SEL sel=NSSelectorFromString(name); Method old=class_getInstanceMethod(pad,sel);
        if (!old) continue;
        IMP original=method_getImplementation(old);
        IMP replacement=imp_implementationWithBlock(^(id object,NSSet<UITouch *> *touches,UIEvent *event) {
            if ([NXKActiveManager swallowsNativeTouchFor:object]) return;
            if ([NXKActiveManager suppressPointerTouches]) {
                NSMutableSet *fingers=[NSMutableSet set];
                for (UITouch *touch in touches) if (touch.type!=UITouchTypeIndirectPointer) [fingers addObject:touch];
                if (fingers.count) ((void(*)(id,SEL,NSSet *,UIEvent *))original)(object,sel,fingers,event);
            } else ((void(*)(id,SEL,NSSet *,UIEvent *))original)(object,sel,touches,event);
        });
        class_replaceMethod(pad,sel,replacement,method_getTypeEncoding(old));
    }
    SEL push=NSSelectorFromString(@"pushGame");
    Method pushMethod=class_getInstanceMethod(pad,push);
    if (pushMethod) {
        IMP original=method_getImplementation(pushMethod);
        IMP replacement=imp_implementationWithBlock(^(UIView *object) {
            if (![NXKActiveManager pushMappedNativeTouches:object])
                ((void(*)(id,SEL))original)(object,push);
        });
        class_replaceMethod(pad,push,replacement,method_getTypeEncoding(pushMethod));
    }
    installed=YES;
}

static bool NXKContains(const struct segment_command_64 *s,uintptr_t address,size_t size,int protection) {
    return (s->initprot & protection)==protection && address>=s->vmaddr &&
        size<=s->vmsize && address-s->vmaddr<=s->vmsize-size;
}
static uintptr_t NXKCheckImage(const struct mach_header *header) {
    if (!header || header->magic!=MH_MAGIC_64) return 0;
    const struct mach_header_64 *h=(const void *)header;
    if (h->cputype!=CPU_TYPE_ARM64 || h->ncmds>512 || h->sizeofcmds>65536) return 0;
    const uint8_t *uuid=NULL;
    const struct segment_command_64 *text=NULL,*data=NULL;
    const uint8_t *cursor=(const uint8_t *)(h+1),*end=cursor+h->sizeofcmds;
    for (unsigned i=0;i<h->ncmds;i++) {
        if (cursor+sizeof(struct load_command)>end) return 0;
        const struct load_command *lc=(const void *)cursor;
        if (lc->cmdsize<sizeof(*lc) || lc->cmdsize>(size_t)(end-cursor)) return 0;
        if (lc->cmd==LC_UUID && lc->cmdsize>=sizeof(struct uuid_command)) uuid=((const struct uuid_command *)lc)->uuid;
        if (lc->cmd==LC_SEGMENT_64 && lc->cmdsize>=sizeof(struct segment_command_64)) {
            const struct segment_command_64 *seg=(const void *)lc;
            if (!strncmp(seg->segname,"__TEXT",16)) text=seg;
            if (!strncmp(seg->segname,"__DATA",16)) data=seg;
        }
        cursor+=lc->cmdsize;
    }
    if (!uuid || !nxk_uuid_family(uuid) || !text || !data || text->vmaddr!=0x100000000ull) return 0;
    if (!NXKContains(text,text->vmaddr+NXK_SETTER_OFFSET,NXK_SETTER_SIZE,5) ||
        !NXKContains(text,text->vmaddr+NXK_FILL_OFFSET,NXK_FILL_SIZE,5) ||
        !NXKContains(data,text->vmaddr+NXK_SAMPLE_OFFSET,8,3) ||
        !NXKContains(data,text->vmaddr+NXK_RENDER_FRAME_OFFSET,8,3) ||
        !NXKContains(data,text->vmaddr+NXK_BUTTON_OFFSET,28,3)) return 0;
    uintptr_t base=(uintptr_t)header;
    uint8_t hash[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256((const void *)(base+NXK_SETTER_OFFSET),NXK_SETTER_SIZE,hash);
    if (memcmp(hash,nxk_setter_hash,sizeof(hash))) return 0;
    CC_SHA256((const void *)(base+NXK_FILL_OFFSET),NXK_FILL_SIZE,hash);
    if (memcmp(hash,nxk_fill_hash,sizeof(hash))) return 0;
    return base;
}

/* Additional exact-build checks guard every private touch/layout entry used below. */
static BOOL NXKCodeHash(uintptr_t base,unsigned offset,unsigned size,const char *expected) {
    uint8_t hash[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256((const void *)(base+offset),size,hash);
    static const char hex[]="0123456789abcdef";
    for (unsigned i=0;i<sizeof(hash);i++)
        if (expected[i*2]!=hex[hash[i]>>4] || expected[i*2+1]!=hex[hash[i]&15]) return NO;
    return YES;
}
static BOOL NXKCheckTouchCode(uintptr_t base) {
    return NXKCodeHash(base,0xce25cu,0xbcu,"1c38f279e4120517792001af1de9dfaf12fc44e684fc914c0524343feca50e59") &&
        NXKCodeHash(base,0xcceccu,0x5acu,"0230c6ca1140f9e558bc4e1a427a570d93a9642c4f41e3452cef27359e5810b9") &&
        NXKCodeHash(base,0xcd478u,0x1a8u,"657c092e4bb6da111442b29ddc3d0f573b7b98dc8ff414388a6bbffcc3041288") &&
        NXKCodeHash(base,0xc6480u,0x180u,"fd75a1e2c1c8c7eaa5c08df6c8b6a976e10140fba038183c8ee63eb8ba382dd3") &&
        NXKCodeHash(base,0xd2d98u,0x28cu,"86f363ddb8b0d4c756be5b183c264e1ddf7ba2140d77c759a900878e5c69c20a");
}

@implementation NXKManager
- (void)start {
    self.captureAction=-1;
    self.resourceExporter=[NXKResourceExporter new];
    NXKActiveManager=self;
    self.pointerRoots=[NSHashTable weakObjectsHashTable];
    self.performanceSamples=[NSMutableArray array];
    self.stallCapture=[[NXKStallCapture alloc] initWithLiveImage:&NXKLiveImage epoch:&NXKImageEpoch];
    nxk_defaults(&input);
    self.prefs=[[NSUserDefaults alloc] initWithSuiteName:@"org.nxkeyboard.botw.v1"];
    NSArray *saved=[self.prefs arrayForKey:@"bindings"];
    if (saved && saved.count<=NXK_ACTION_COUNT) {
        for (unsigned a=0;a<saved.count;a++) {
            if (![saved[a] isKindOfClass:NSArray.class]) continue;
            NSArray *keys=saved[a];
            if (keys.count>3) continue;
            BOOL valid=YES;
            for (id key in keys) if (![key isKindOfClass:NSNumber.class] || [key integerValue]<0 || [key integerValue]>=NXK_INPUT_COUNT) valid=NO;
            if (!valid) continue;
            memset(input.bindings[a].keys,0,sizeof(input.bindings[a].keys));
            for (unsigned k=0;k<keys.count;k++) input.bindings[a].keys[k]=[keys[k] unsignedIntValue];
        }
    }
    if (saved.count>0 && saved.count<=NXK_POINTER_TOGGLE) {
        for (unsigned a=0;a<saved.count;a++) for (unsigned i=0;i<3;i++)
            if (input.bindings[a].keys[i]==41) memset(input.bindings[NXK_POINTER_TOGGLE].keys,0,sizeof(input.bindings[NXK_POINTER_TOGGLE].keys));
    }
    if ([self.prefs integerForKey:@"mouseBindingsVersion"]<1) {
        NXKInput defaults; nxk_defaults(&defaults);
        for (unsigned a=0;a<NXK_ACTION_COUNT;a++) for (unsigned i=0;i<3;i++) {
            unsigned code=defaults.bindings[a].keys[i];
            if (code<256) continue;
            BOOL exists=NO;
            for (unsigned j=0;j<3;j++) if (input.bindings[a].keys[j]==code) exists=YES;
            if (!exists) for (unsigned j=0;j<3;j++) if (!input.bindings[a].keys[j]) { input.bindings[a].keys[j]=code; break; }
        }
        [self.prefs setInteger:1 forKey:@"mouseBindingsVersion"];
    }
    NSInteger camera=[self.prefs integerForKey:@"camera"];
    if (camera>=4000 && camera<=32767) input.camera_max=(int32_t)camera;
    self.enabled=[self.prefs boolForKey:@"enabled"];
    self.enhancedTouch=[self.prefs objectForKey:@"enhancedTouch"]?[self.prefs boolForKey:@"enhancedTouch"]:YES;
    double touchScale=[self.prefs doubleForKey:@"touchScale"];
    self.touchScale=(isfinite(touchScale) && touchScale>=0.8 && touchScale<=1.2)?touchScale:1;
    double touchSensitivity=[self.prefs doubleForKey:@"touchSensitivity"];
    self.touchSensitivity=(isfinite(touchSensitivity) && touchSensitivity>=0.25 && touchSensitivity<=3)?touchSensitivity:1;
    self.invertTouchY=[self.prefs boolForKey:@"invertTouchY"];
    self.display=[NXKDisplayController new];
    if ([self.prefs objectForKey:@"displayMode"]) self.display.mode=[self.prefs integerForKey:@"displayMode"];
    self.display.immersive=[self.prefs objectForKey:@"immersive"]?[self.prefs boolForKey:@"immersive"]:YES;
    __weak NXKManager *displayOwner=self;
    self.display.permitsRoot=^BOOL(UIViewController *root) {
        NXKManager *owner=displayOwner;
        return owner.targetReady && atomic_load(&NXKLiveImage)==owner.targetBase &&
            root.viewIfLoaded.window.windowScene==owner.overlay.windowScene;
    };
    self.hideTouchControls=[self.prefs boolForKey:@"hideTouchControls"];
    self.mouseLook=[self.prefs objectForKey:@"mouseLook"]?[self.prefs boolForKey:@"mouseLook"]:YES;
    self.lockPointer=[self.prefs objectForKey:@"lockPointer"]?[self.prefs boolForKey:@"lockPointer"]:YES;
    self.invertMouseY=[self.prefs boolForKey:@"invertMouseY"];
    double sensitivity=[self.prefs doubleForKey:@"mouseSensitivity"];
    self.mouseSensitivity=(isfinite(sensitivity) && sensitivity>=0.25 && sensitivity<=4)?sensitivity:1;
    [self save];
    self.error=@"未找到匹配的游戏版本";
    NSNotificationCenter *nc=NSNotificationCenter.defaultCenter;
    [nc addObserver:self selector:@selector(resign:) name:UIApplicationWillResignActiveNotification object:nil];
    [nc addObserver:self selector:@selector(resign:) name:UIApplicationDidEnterBackgroundNotification object:nil];
    [nc addObserver:self selector:@selector(resume:) name:UIApplicationDidBecomeActiveNotification object:nil];
    [nc addObserver:self selector:@selector(resignScene:) name:UISceneWillDeactivateNotification object:nil];
    [nc addObserver:self selector:@selector(resumeScene:) name:UISceneDidActivateNotification object:nil];
    [nc addObserver:self selector:@selector(keyboardsChanged:) name:GCKeyboardDidConnectNotification object:nil];
    [nc addObserver:self selector:@selector(keyboardsChanged:) name:GCKeyboardDidDisconnectNotification object:nil];
    [nc addObserver:self selector:@selector(miceChanged:) name:GCMouseDidConnectNotification object:nil];
    [nc addObserver:self selector:@selector(miceChanged:) name:GCMouseDidDisconnectNotification object:nil];
    [nc addObserver:self selector:@selector(miceChanged:) name:GCMouseDidBecomeCurrentNotification object:nil];
    [nc addObserver:self selector:@selector(pointerStateChanged:) name:UIPointerLockStateDidChangeNotification object:nil];
    [nc addObserver:self selector:@selector(windowChanged:) name:UIWindowDidBecomeKeyNotification object:nil];
    for (NSString *name in @[UITextFieldTextDidBeginEditingNotification,UITextFieldTextDidEndEditingNotification,UITextViewTextDidBeginEditingNotification,UITextViewTextDidEndEditingNotification])
        [nc addObserver:self selector:@selector(textContextChanged:) name:name object:nil];
    self.active=UIApplication.sharedApplication.applicationState==UIApplicationStateActive;
    self.timer=[NSTimer timerWithTimeInterval:0.25 target:self selector:@selector(tick:) userInfo:nil repeats:YES];
    self.inputTimerInterval=0.25;
    [NSRunLoop.mainRunLoop addTimer:self.timer forMode:NSRunLoopCommonModes];
    [self tick:nil];
    NSLog(@"[NXKeyboard] v0.3.2 loaded; waiting for the matching DmIOS image");
}
- (void)save {
    NSMutableArray *bindings=[NSMutableArray array];
    for (unsigned a=0;a<NXK_ACTION_COUNT;a++) {
        NSMutableArray *keys=[NSMutableArray array];
        for (unsigned k=0;k<3;k++) if (input.bindings[a].keys[k]) [keys addObject:@(input.bindings[a].keys[k])];
        [bindings addObject:keys];
    }
    [self.prefs setObject:bindings forKey:@"bindings"];
    [self.prefs setInteger:input.camera_max forKey:@"camera"];
    [self.prefs setBool:self.enabled forKey:@"enabled"];
    [self.prefs setBool:self.enhancedTouch forKey:@"enhancedTouch"];
    [self.prefs setDouble:self.touchScale forKey:@"touchScale"];
    [self.prefs setDouble:self.touchSensitivity forKey:@"touchSensitivity"];
    [self.prefs setBool:self.invertTouchY forKey:@"invertTouchY"];
    [self.prefs setInteger:self.display.mode forKey:@"displayMode"];
    [self.prefs setBool:self.display.immersive forKey:@"immersive"];
    [self.prefs setBool:self.hideTouchControls forKey:@"hideTouchControls"];
    [self.prefs setBool:self.mouseLook forKey:@"mouseLook"];
    [self.prefs setBool:self.lockPointer forKey:@"lockPointer"];
    [self.prefs setBool:self.invertMouseY forKey:@"invertMouseY"];
    [self.prefs setDouble:self.mouseSensitivity forKey:@"mouseSensitivity"];
}
- (UIView *)findTouchPad:(UIView *)view {
    Class cls=NSClassFromString(@"DmGamepad");
    if (cls && [view isKindOfClass:cls]) return view;
    for (UIView *child in view.subviews) {
        UIView *found=[self findTouchPad:child];
        if (found) return found;
    }
    return nil;
}
- (BOOL)swallowsNativeTouchFor:(UIView *)pad {
    return self.targetReady && self.touchIntegrationReady && self.enhancedTouch &&
        atomic_load(&NXKLiveImage)==self.targetBase && [self slotAvailable] &&
        pad==self.touchPad && self.nativeTouchTaken;
}
- (void)sendDirectTouches:(const NXKGameTouch *)points count:(int)count {
    if (!self.touchIntegrationReady || !self.targetReady || atomic_load(&NXKLiveImage)!=self.targetBase) return;
    if (count<0 || count>16 || (count && !points)) return;
    ((void(*)(const NXKGameTouch *,int))(self.targetBase+0xc6480u))(points,count);
}
- (void)resetNativeTouch {
    if (!self.touchIntegrationReady || !self.targetReady || atomic_load(&NXKLiveImage)!=self.targetBase || !self.touchPad) return;
    SEL selector=NSSelectorFromString(@"reset");
    Method method=class_getInstanceMethod(object_getClass(self.touchPad),selector);
    IMP implementation=method?method_getImplementation(method):NULL;
    if ((uintptr_t)implementation==self.targetBase+0xce25cu)
        ((void(*)(id,SEL))implementation)(self.touchPad,selector);
}
- (BOOL)pushMappedNativeTouches:(UIView *)pad {
    if (!self.touchIntegrationReady || !self.targetReady || atomic_load(&NXKLiveImage)!=self.targetBase ||
        pad!=self.touchPad || !self.displayHooksReady) return NO;
    if ([self swallowsNativeTouchFor:pad]) return YES;
    Ivar ivar=class_getInstanceVariable(object_getClass(pad),"_game");
    if (!ivar || strcmp(ivar_getTypeEncoding(ivar),"[16@\"UITouch\"]")) return NO;
    CGRect rect=[self.display displayRectInView:pad];
    if (CGRectIsNull(rect)) return NO;
    NXKGameTouch points[16]; int count=0;
    const uint8_t *slots=(const uint8_t *)(__bridge void *)pad+ivar_getOffset(ivar);
    for (int i=0;i<16;i++) {
        void *address=NULL; memcpy(&address,slots+i*sizeof(address),sizeof(address));
        UITouch *touch=(__bridge UITouch *)address;
        if (!touch || touch.phase==UITouchPhaseEnded || touch.phase==UITouchPhaseCancelled) continue;
        CGPoint normalized;
        if ([self.display normalizedGamePoint:[touch locationInView:pad] inView:pad result:&normalized])
            points[count++]=(NXKGameTouch){i,(float)fmin(0.999999,normalized.x),(float)fmin(0.999999,normalized.y)};
    }
    [self sendDirectTouches:points count:count];
    return YES;
}
- (void)updateDisplay {
    if (!self.targetReady || atomic_load(&NXKLiveImage)!=self.targetBase) return;
    self.display.targetBase=self.targetBase;
    self.display.eligible=self.touchIntegrationReady;
    self.display.settingsVisible=self.suspended;
    self.displayHooksReady=[self.display installHooks];
    [self.display refreshInScene:self.overlay.windowScene];
}
- (void)updateTouchControls {
    if (!self.targetReady || atomic_load(&NXKLiveImage)!=self.targetBase) return;
    if (!self.touchPad.window) {
        double now=CACurrentMediaTime();
        if (self.lastTouchScan && now-self.lastTouchScan<0.25) return;
        self.lastTouchScan=now;
        [self.touchView resetInput]; [self.touchView removeFromSuperview]; self.touchView=nil;
        self.nativeTouchTaken=NO;
        self.touchPad=nil; self.touchControls=nil; self.controlsHiddenByPlugin=NO;
        for (UIWindow *window in self.overlay.windowScene.windows) {
            if (window==self.overlay) continue;
            UIView *view=[self findTouchPad:window];
            if (view) { self.touchPad=view; break; }
        }
        if (self.touchPad) {
            Ivar ivar=class_getInstanceVariable(object_getClass(self.touchPad),"_controls");
            id controls=ivar?object_getIvar(self.touchPad,ivar):nil;
            if ([controls isKindOfClass:CALayer.class]) self.touchControls=controls;
        }
    }
    if (!self.touchPad) return;
    BOOL takeover=self.enhancedTouch && self.touchIntegrationReady && [self slotAvailable];
    if (takeover!=self.nativeTouchTaken) {
        [self.touchView resetInput];
        [self resetNativeTouch];
        self.nativeTouchTaken=takeover;
    }
    if (takeover && !self.touchView) {
        self.touchPad.multipleTouchEnabled=YES;
        NXKTouchView *view=[[NXKTouchView alloc] initWithFrame:self.touchPad.bounds];
        self.touchView=view;
        __weak NXKManager *owner=self;
        view.inputChanged=^{ [owner publish]; };
        view.cancelledButtons=^(uint64_t cancelled) {
            NXKManager *manager=owner;
            if (manager.ownsSlot && [manager slotAvailable])
                atomic_fetch_and(manager->writer.latch,~(cancelled & ~manager.lastPublishedOutput.buttons));
        };
        view.mapGamePoint=^BOOL(CGPoint point,CGPoint *normalized) {
            NXKManager *manager=owner;
            CGPoint padPoint=[manager.touchView convertPoint:point toView:manager.touchPad];
            return [manager.display normalizedGamePoint:padPoint inView:manager.touchPad result:normalized];
        };
        view.directTouchesChanged=^(const NXKGameTouch *points,int count) {
            NXKManager *manager=owner;
            if (!count || (manager.touchView.active && !manager.suspended && [manager gameIsForeground] &&
                !manager.touchPad.window.rootViewController.presentedViewController && [manager slotAvailable]))
                [manager sendDirectTouches:points count:count];
        };
        [self.touchPad addSubview:view];
    }
    self.touchView.controlScale=self.touchScale;
    self.touchView.lookSensitivity=self.touchSensitivity;
    self.touchView.invertY=self.invertTouchY;
    self.touchView.controlsHidden=self.hideTouchControls;
    BOOL hostModal=self.touchPad.window.rootViewController.presentedViewController!=nil;
    self.touchView.active=takeover && !self.suspended && !hostModal && [self gameIsForeground];
    CALayer *controls=self.touchControls;
    BOOL hidden=self.hideTouchControls || takeover;
    if (!hidden) for (GCController *pad in GCController.controllers)
        if (pad.extendedGamepad) { hidden=YES; break; }
    if (controls && (hidden || self.controlsHiddenByPlugin) && controls.hidden!=hidden) {
        [CATransaction begin]; [CATransaction setDisableActions:YES];
        controls.hidden=hidden; [CATransaction commit];
    }
    self.controlsHiddenByPlugin=self.hideTouchControls || takeover;
}
- (void)setEnhancedTouchEnabled:(BOOL)value {
    [self clear];
    [self resetNativeTouch];
    self.enhancedTouch=value;
    [self save]; [self updateTouchControls]; [self refresh];
}
- (void)setTouchControlsHidden:(BOOL)hidden {
    self.hideTouchControls=hidden;
    [self save]; [self updateTouchControls];
}
- (BOOL)slotAvailable {
    if (!self.targetReady || atomic_load(&NXKLiveImage)!=self.targetBase) return NO;
    /* DM_KEYS is initialized before gameplay; its nonzero step count persists. */
    int count=__atomic_load_n((int *)(self.targetBase+NXK_SCRIPT_COUNT_OFFSET),__ATOMIC_ACQUIRE);
    const char *env=getenv("DM_KEYS");
    return count==0 && (!env || !*env);
}
- (void)clear {
    nxk_release(&input);
    nxk_mouse_clear(&mouseMotion);
    [self.touchView resetInput];
    self.lastPublishedOutput=(NXKOutput){0,0};
    self.previousToggle=NO;
    if (self.ownsSlot && [self slotAvailable]) nxk_neutral(&writer);
    self.ownsSlot=NO;
}
- (void)blockPhysicalKeys {
    for (unsigned k=1;k<256;k++)
        if ([self.keyboard buttonForKeyCode:(GCKeyCode)k].isPressed) input.blocked[k]=true;
    [self blockMouseButtons];
}
- (void)blockMouseButtons {
    for (unsigned i=0;i<self.mouseButtons.count;i++) {
        id button=self.mouseButtons[i];
        if ([button isKindOfClass:GCControllerButtonInput.class] && [button isPressed]) input.blocked[256+i]=true;
    }
}
- (void)setMappingEnabled:(BOOL)value {
    [self clear];
    [self blockPhysicalKeys];
    self.enabled=value;
    self.mouseReleased=NO;
    [self save];
    [self refresh];
}
- (void)resign:(NSNotification *)note { (void)note; self.active=NO; [self clear]; [self refresh]; }
- (void)resume:(NSNotification *)note { (void)note; [self clear]; [self blockPhysicalKeys]; self.active=YES; [self refresh]; }
- (void)resignScene:(NSNotification *)note {
    if (note.object==self.overlay.windowScene) [self resign:note];
}
- (void)resumeScene:(NSNotification *)note {
    if (note.object==self.overlay.windowScene) [self resume:note];
}
- (void)keyboardsChanged:(NSNotification *)note { (void)note; [self attachKeyboard]; }
- (void)attachKeyboard {
    GCKeyboard *device=GCKeyboard.coalescedKeyboard;
    GCKeyboardInput *profile=device.keyboardInput;
    if (profile==self.keyboard) return;
    [self clear];
    if (self.keyboard.keyChangedHandler==self.ownHandler) self.keyboard.keyChangedHandler=self.previousHandler;
    self.keyboard=profile;
    self.previousHandler=profile.keyChangedHandler;
    if (!profile) { self.ownHandler=nil; [self refresh]; return; }
    [self blockPhysicalKeys];
    __weak NXKManager *weakSelf=self;
    GCKeyboardValueChangedHandler previous=self.previousHandler;
    self.ownHandler=^(GCKeyboardInput *kb,GCDeviceButtonInput *key,GCKeyCode code,BOOL down) {
        if (previous) previous(kb,key,code,down);
        /* Keep the existing device's handler queue untouched. */
        dispatch_async(dispatch_get_main_queue(),^{
            NXKManager *strongSelf=weakSelf;
            if (kb==strongSelf.keyboard) [strongSelf key:code down:down];
        });
    };
    profile.keyChangedHandler=self.ownHandler;
    [self refresh];
}
- (void)miceChanged:(NSNotification *)note { (void)note; if (self.targetReady) [self attachMouse]; }
- (void)detachMouse {
    if (self.mouse.mouseMovedHandler==self.ownMouseMoved) self.mouse.mouseMovedHandler=self.previousMouseMoved;
    if (self.mouse.scroll.valueChangedHandler==self.ownScroll) self.mouse.scroll.valueChangedHandler=self.previousScroll;
    for (unsigned i=0;i<self.mouseButtons.count;i++) {
        id button=self.mouseButtons[i];
        if (![button isKindOfClass:GCControllerButtonInput.class]) continue;
        if ([button valueChangedHandler]==self.ownMouseButtons[i])
            [button setValueChangedHandler:self.previousMouseButtons[i]==NSNull.null?nil:self.previousMouseButtons[i]];
    }
    self.mouse=nil; self.mouseButtons=nil; self.previousMouseButtons=nil; self.ownMouseButtons=nil;
    self.previousMouseMoved=nil; self.ownMouseMoved=nil; self.previousScroll=nil; self.ownScroll=nil;
}
- (void)attachMouse {
    GCMouse *device=GCMouse.current?:GCMouse.mice.firstObject;
    GCMouseInput *profile=device.mouseInput;
    if (profile==self.mouse) return;
    [self clear]; [self detachMouse]; self.mouse=profile;
    if (!profile) { [self refresh]; return; }
    __weak NXKManager *weakSelf=self;
    self.previousMouseMoved=profile.mouseMovedHandler;
    GCMouseMoved previousMoved=self.previousMouseMoved;
    self.ownMouseMoved=^(GCMouseInput *mouse,float dx,float dy) {
        if (previousMoved) previousMoved(mouse,dx,dy);
        dispatch_async(dispatch_get_main_queue(),^{
            NXKManager *s=weakSelf;
            if (mouse==s.mouse && s.enabled && s.mouseLook && !s.mouseReleased && !s.suspended && [s gameIsForeground])
                nxk_mouse_move(&s->mouseMotion,dx,dy);
        });
    };
    profile.mouseMovedHandler=self.ownMouseMoved;
    NSMutableArray *buttons=[NSMutableArray arrayWithArray:@[profile.leftButton?:NSNull.null,profile.rightButton?:NSNull.null,profile.middleButton?:NSNull.null]];
    for (GCControllerButtonInput *button in profile.auxiliaryButtons) { if (buttons.count==8) break; [buttons addObject:button]; }
    self.mouseButtons=buttons;
    NSMutableArray *previouss=[NSMutableArray array],*owns=[NSMutableArray array];
    for (unsigned i=0;i<buttons.count;i++) {
        id object=buttons[i];
        if (![object isKindOfClass:GCControllerButtonInput.class]) { [previouss addObject:NSNull.null]; [owns addObject:NSNull.null]; continue; }
        GCControllerButtonInput *button=object;
        GCControllerButtonValueChangedHandler previous=button.valueChangedHandler;
        [previouss addObject:(id)previous?:NSNull.null];
        GCControllerButtonValueChangedHandler own=^(GCControllerButtonInput *b,float value,BOOL down) {
            if (previous) previous(b,value,down);
            dispatch_async(dispatch_get_main_queue(),^{
                NXKManager *s=weakSelf;
                if (s.mouse==profile) [s mouseButton:256+i down:down];
            });
        };
        [owns addObject:[own copy]]; button.valueChangedHandler=own;
    }
    self.previousMouseButtons=previouss; self.ownMouseButtons=owns;
    self.previousScroll=profile.scroll.valueChangedHandler;
    GCControllerDirectionPadValueChangedHandler previousScroll=self.previousScroll;
    self.ownScroll=^(GCControllerDirectionPad *scroll,float x,float y) {
        if (previousScroll) previousScroll(scroll,x,y);
        dispatch_async(dispatch_get_main_queue(),^{
            NXKManager *s=weakSelf;
            if (s.mouse!=profile || !isfinite(y) || y==0) return;
            unsigned code=y>0?NXK_WHEEL_UP:NXK_WHEEL_DOWN;
            [s mouseButton:code down:YES];
            dispatch_after(dispatch_time(DISPATCH_TIME_NOW,60*NSEC_PER_MSEC),dispatch_get_main_queue(),^{
                if (s.mouse==profile) [s mouseButton:code down:NO];
            });
        });
    };
    profile.scroll.valueChangedHandler=self.ownScroll;
    [self blockPhysicalKeys]; [self refresh];
}
- (void)mouseButton:(unsigned)code down:(BOOL)down {
    if (self.captureAction>=0) return;
    if (self.mouseReleased && self.captureAction<0) {
        if (down) input.blocked[code]=true;
        else nxk_key(&input,code,false);
        return;
    }
    [self inputEvent:code down:down];
}
- (BOOL)suppressPointerTouches {
    return self.targetReady && self.enabled && self.mouse && !self.mouseReleased && !self.suspended && [self gameIsForeground];
}
- (BOOL)wantsPointerLock {
    return self.lockPointer && self.mouseLook && [self suppressPointerTouches];
}
- (BOOL)ownsPointerRoot:(UIViewController *)controller {
    return controller && self.targetReady && [self.pointerRoots containsObject:controller];
}
- (void)windowChanged:(NSNotification *)note {
    (void)note; self.lastRootScan=0; pointerPolicy.initialized=false;
    [self updatePointerPreference];
}
- (void)pointerStateChanged:(NSNotification *)note {
    UIScene *scene=note.userInfo[UIPointerLockStateSceneUserInfoKey];
    if (scene && scene!=self.overlay.windowScene) return;
    if (!self.suspended) self.gamePointerLocked=self.overlay.windowScene.pointerLockState.isLocked;
}
- (void)textContextChanged:(NSNotification *)note {
    (void)note; self.lastContextScan=0; [self updateTextContext];
    if (self.textInputBlocked) [self clear];
    [self refresh];
}
- (void)updateTextContext {
    double now=CACurrentMediaTime();
    if (self.lastContextScan && now-self.lastContextScan<0.1) return;
    self.lastContextScan=now;
    BOOL blocked=NO;
    for (UIWindow *window in self.overlay.windowScene.windows) {
        if (window!=self.overlay && !window.hidden && [self textInputInView:window]) { blocked=YES; break; }
    }
    self.textInputBlocked=blocked;
}
- (void)updatePointerPreference {
    if (!self.overlay) return;
    double now=CACurrentMediaTime();
    if (!self.lastRootScan || now-self.lastRootScan>=0.25) {
        self.lastRootScan=now;
        NSMutableArray *roots=[NSMutableArray array];
        for (UIWindow *window in self.overlay.windowScene.windows) {
            if (window.hidden || !window.rootViewController) continue;
            UIViewController *root=window.rootViewController;
            Class gameRoot=NSClassFromString(@"DmRootVC");
            if (window!=self.overlay && window!=self.touchPad.window && !(gameRoot && [root isKindOfClass:gameRoot])) continue;
            [roots addObject:root];
            if (window!=self.overlay) NXKHookPointerRootClass(object_getClass(root));
        }
        if (![roots isEqualToArray:self.pointerRootList]) {
            [self.pointerRoots removeAllObjects];
            for (UIViewController *root in roots) [self.pointerRoots addObject:root];
            self.pointerRootList=roots; self.pointerRootGeneration++;
        }
    }
    BOOL wanted=[self wantsPointerLock];
    self.pointerRequested=wanted;
    BOOL locked=self.overlay.windowScene.pointerLockState.isLocked;
    if (nxk_pointer_update(&pointerPolicy,wanted,locked,self.pointerRootGeneration,now)) {
        for (UIViewController *root in self.pointerRootList) [root setNeedsUpdateOfPrefersPointerLocked];
    }
}
- (void)setMouseModeReleased:(BOOL)released {
    self.mouseReleased=released;
    nxk_mouse_clear(&mouseMotion);
    uint64_t before=self.lastPublishedOutput.buttons;
    nxk_release_mouse(&input);
    uint64_t sampled=self.targetReady && atomic_load(&NXKLiveImage)==self.targetBase?
        atomic_load((_Atomic uint64_t *)(self.targetBase+NXK_SAMPLE_OFFSET)):0;
    NXKOutput touch=self.touchView.active?[self.touchView outputAtTime:CACurrentMediaTime() sample:sampled]:(NXKOutput){0,0};
    uint64_t cancelled=before & ~(nxk_evaluate(&input).buttons | touch.buttons);
    if (self.ownsSlot && [self slotAvailable]) atomic_fetch_and(writer.latch,~cancelled);
    [self blockMouseButtons]; [self publish]; [self refresh];
}
- (BOOL)textInputInView:(UIView *)view {
    if (view.isFirstResponder && ([view conformsToProtocol:@protocol(UITextInput)] || [view conformsToProtocol:@protocol(UIKeyInput)])) return YES;
    for (UIView *child in view.subviews) if ([self textInputInView:child]) return YES;
    return NO;
}
- (BOOL)gameIsForeground {
    if (!self.active || UIApplication.sharedApplication.applicationState!=UIApplicationStateActive) return NO;
    if (self.overlay.windowScene.activationState!=UISceneActivationStateForegroundActive) return NO;
    return !self.textInputBlocked;
}
- (void)key:(GCKeyCode)code down:(BOOL)down {
    if (code<=0 || code>=256) return;
    [self inputEvent:(unsigned)code down:down];
}
- (void)inputEvent:(unsigned)code down:(BOOL)down {
    if (!code || code>=NXK_INPUT_COUNT) return;
    if (self.captureAction>=0) {
        if (down && self.active) {
            NSInteger action=self.captureAction;
            [self bindCode:code action:action];
            input.blocked[code]=true;
            [self finishCapture];
        }
        return;
    }
    if (!self.suspended && [self gameIsForeground]) {
        BOOL wasPointer=nxk_active(&input,NXK_POINTER_TOGGLE);
        BOOL fresh=nxk_key(&input,(unsigned)code,down);
        BOOL toggle=nxk_active(&input,NXK_TOGGLE);
        if (fresh && toggle && !self.previousToggle) [self setMappingEnabled:!self.enabled];
        self.previousToggle=nxk_active(&input,NXK_TOGGLE);
        if (fresh && nxk_active(&input,NXK_POINTER_TOGGLE) && !wasPointer && self.enabled && self.mouse) {
            input.blocked[code]=true; input.pressed[code]=false;
            [self setMouseModeReleased:!self.mouseReleased];
        }
        [self publish];
    } else {
        if (down) input.blocked[code]=true;
        else nxk_key(&input,(unsigned)code,false);
        [self clear];
    }
}
- (void)publish {
    BOOL keyboardInput=self.enabled && (self.keyboard || self.mouse);
    BOOL touchInput=self.enhancedTouch && self.touchIntegrationReady && self.touchView.active;
    BOOL hostModal=self.touchPad.window.rootViewController.presentedViewController!=nil;
    if ((!keyboardInput && !touchInput) || self.suspended || hostModal || ![self gameIsForeground] || ![self slotAvailable]) {
        [self clear]; return;
    }
    uint64_t sampled=atomic_load((_Atomic uint64_t *)(self.targetBase+NXK_SAMPLE_OFFSET));
    NXKOutput physical=keyboardInput?nxk_mouse_merge(&input,&mouseMotion):(NXKOutput){0,0};
    NXKOutput touch=touchInput?[self.touchView outputAtTime:CACurrentMediaTime() sample:sampled]:(NXKOutput){0,0};
    NXKOutput output=nxk_touch_merge(physical,touch);
    self.ownsSlot=YES;
    nxk_publish(&writer,output);
    self.lastPublishedOutput=output;
    uint64_t cancelled=[self.touchView takeCancelledButtons];
    if (cancelled) atomic_fetch_and(writer.latch,~(cancelled & ~output.buttons));
}
- (void)findTarget {
    for (unsigned i=0;i<_dyld_image_count();i++) {
        uintptr_t base=NXKCheckImage(_dyld_get_image_header(i));
        if (!base) continue;
        self.targetBase=base;
        writer=(NXKWriter){(_Atomic uint64_t *)(base+NXK_BUTTON_OFFSET),(_Atomic uint64_t *)(base+NXK_LATCH_OFFSET),(_Atomic uint64_t *)(base+NXK_AXES_OFFSET)};
        self.targetReady=YES; self.error=nil;
        atomic_store(&NXKLiveImage,base);
        atomic_fetch_add(&NXKImageEpoch,1);
        nxk_probe_init(&renderProbe,base);
        self.touchIntegrationReady=NXKCheckTouchCode(base);
        NXKInstallPointerHooks();
        [self updateDisplay];
        NSLog(@"[NXKeyboard] DmIOS input and code identity verified");
        [self attachKeyboard];
        [self attachMouse];
        break;
    }
}
- (void)tick:(NSTimer *)timer {
    (void)timer;
    if (self.targetReady && atomic_load(&NXKLiveImage)!=self.targetBase) {
        [self clear]; self.targetReady=NO; self.targetBase=0;
        [self.display restore];
        [self.touchView removeFromSuperview]; self.touchView=nil;
        self.nativeTouchTaken=NO; self.touchIntegrationReady=NO; self.displayHooksReady=NO;
        self.overlay.hidden=YES;
        self.overlay=nil; self.button=nil; self.captureAlert=nil;
        [self.pointerRoots removeAllObjects]; self.pointerRootList=nil;
        self.lastRootScan=0; pointerPolicy.initialized=false;
        self.lastButtonTitle=nil;
        self.lastTouchScan=0; self.lastDeviceScan=0;
        [self.performanceSamples removeAllObjects];
        self.lastGameplayPointer=nil; self.lastGameplayDiagnostics=0;
        memset(&renderProbe,0,sizeof(renderProbe));
        self.touchPad=nil; self.touchControls=nil; self.controlsHiddenByPlugin=NO;
        self.captureAction=-1; self.suspended=NO;
        if (self.keyboard.keyChangedHandler==self.ownHandler) self.keyboard.keyChangedHandler=self.previousHandler;
        self.keyboard=nil; self.ownHandler=nil; self.previousHandler=nil;
        [self detachMouse];
    }
    if (!self.targetReady) [self findTarget];
    BOOL isGuest=[NSBundle.mainBundle.bundleIdentifier isEqualToString:@"org.nxnative.01007ef00011e000"];
    if (self.targetReady || isGuest) [self ensureOverlay];
    if (!self.targetReady) { [self updateInputTimer]; return; }
    [self updateTextContext];
    double displayTime=CACurrentMediaTime();
    if (!self.lastDisplayScan || displayTime-self.lastDisplayScan>=0.25) {
        self.lastDisplayScan=displayTime; [self updateDisplay];
    }
    [self updateTouchControls];
    double deviceScanTime=CACurrentMediaTime();
    if (!self.lastDeviceScan || deviceScanTime-self.lastDeviceScan>=0.25) {
        self.lastDeviceScan=deviceScanTime;
        [self attachKeyboard]; [self attachMouse];
    }
    if (self.captureAction>=0) return;
    /* Poll release transitions as a fallback; callbacks preserve short press edges. */
    for (unsigned k=1;k<256;k++)
        if ((input.pressed[k] || input.blocked[k]) && ![self.keyboard buttonForKeyCode:(GCKeyCode)k].isPressed)
            nxk_key(&input,k,false);
    for (unsigned i=0;i<self.mouseButtons.count;i++) {
        id button=self.mouseButtons[i]; unsigned k=256+i;
        if ((input.pressed[k] || input.blocked[k]) && (![button isKindOfClass:GCControllerButtonInput.class] || ![button isPressed])) nxk_key(&input,k,false);
    }
    double now=CACurrentMediaTime(),dt=self.lastMouseTick?now-self.lastMouseTick:1.0/60.0;
    self.lastMouseTick=now;
    if (self.enabled && self.mouse && self.mouseLook && !self.mouseReleased && !self.suspended && [self gameIsForeground] && [self slotAvailable]) {
        uint64_t sampled=atomic_load((_Atomic uint64_t *)(self.targetBase+NXK_SAMPLE_OFFSET));
        nxk_mouse_tick(&mouseMotion,dt,now,sampled,32*self.mouseSensitivity,self.invertMouseY);
    } else nxk_mouse_clear(&mouseMotion);
    [self publish];
    [self refresh];
    [self sampleDiagnostics];
}
- (void)updateInputTimer {
    BOOL polling=self.targetReady && self.active && !self.suspended && !self.textInputBlocked &&
        ((self.enabled && (self.keyboard || (self.mouse && !self.mouseReleased))) || self.touchView.active);
    double interval=polling?1.0/60.0:0.25;
    if (interval==self.inputTimerInterval) return;
    self.inputTimerInterval=interval;
    [self.timer invalidate];
    self.timer=[NSTimer timerWithTimeInterval:interval target:self selector:@selector(tick:) userInfo:nil repeats:YES];
    [NSRunLoop.mainRunLoop addTimer:self.timer forMode:NSRunLoopCommonModes];
}
- (NSDictionary *)pointerSnapshot {
    NSMutableArray *windows=[NSMutableArray array];
    for (UIWindow *window in self.overlay.windowScene.windows) {
        if (window.hidden) continue;
        UIViewController *root=window.rootViewController;
        [windows addObject:@{@"root":root?NSStringFromClass(object_getClass(root)):@"none",@"keyWindow":@(window.isKeyWindow),@"level":@(window.windowLevel),@"bounds":NSStringFromCGRect(window.bounds),@"pointerManaged":@([self ownsPointerRoot:root]),@"prefersLocked":@(root.prefersPointerLocked)}];
    }
    return @{@"mappingEnabled":@(self.enabled),@"mouseConnected":@(self.mouse!=nil),@"mouseLook":@(self.mouseLook),@"mouseGameMode":@(!self.mouseReleased),@"captureOption":@(self.lockPointer),@"captureRequested":@([self wantsPointerLock]),@"actuallyLocked":@(self.overlay.windowScene.pointerLockState.isLocked),@"foreground":@([self gameIsForeground]),@"assistiveTouch":@(UIAccessibilityIsAssistiveTouchRunning()),@"sceneBounds":NSStringFromCGRect(self.overlay.windowScene.coordinateSpace.bounds),@"screenBounds":NSStringFromCGRect(self.overlay.windowScene.screen.bounds),@"windows":windows};
}
- (void)sampleDiagnostics {
    double now=CACurrentMediaTime();
    if (now-self.lastDiagnostics<1.0 || !self.targetReady || atomic_load(&NXKLiveImage)!=self.targetBase) return;
    self.lastDiagnostics=now;
    if (self.suspended || ![self gameIsForeground]) { renderProbe.time=0; return; }
    self.lastGameplayPointer=[self pointerSnapshot]; self.lastGameplayDiagnostics=now;
    NXKRenderSample sample=nxk_probe_sample(&renderProbe,now);
    if (sample.valid) {
        NSMutableDictionary *row=[@{@"time":@(now),@"presentedFPS":@(sample.fps),@"meanPresentIntervalMs":@(sample.mean_frame_ms),@"renderPassesPerPresentedFrame":@(sample.passes_per_frame),@"drawsPerPresentedFrame":@(sample.draws_per_frame),@"compilerTimeMsInSampleWindow":@(sample.compile_ms)} mutableCopy];
        if (sample.gpu_valid) row[@"latestCompletedGPUFrameSpanMs"]=@(sample.gpu_span_ms);
        [self.performanceSamples addObject:row];
        if (self.performanceSamples.count>60) [self.performanceSamples removeObjectAtIndex:0];
    }
}
- (NSString *)diagnosticText {
    NSMutableDictionary *settings=[NSMutableDictionary dictionary];
    for (NSString *key in @[@"DM_FPS_TARGET",@"DM_FPS_HOLD",@"DM_SLOWFRAME",@"DM_SPIN_POLL",@"DM_RENDER_SCALE",@"DM_PRESENT_LINK",@"DM_PRESENT_LATENCY",@"DM_NVN_ENC_THREAD",@"DM_NVN_VBUF_CACHE",@"DM_NVN_UB_DEFER",@"DM_NVN_UB_PARTIAL",@"DM_NVN_UB_LAZY",@"DM_NVN_LAZY_CLEAR",@"DM_NVN_TBDR"]) {
        const char *value=getenv(key.UTF8String); settings[key]=value?@(value):@"upstream default";
    }
    NSDictionary *report=@{@"plugin":@"NXKeyboard 0.3.2",@"systemVersion":UIDevice.currentDevice.systemVersion,@"hostBundleIdentifier":NSBundle.mainBundle.bundleIdentifier?:@"unknown",@"targetMatched":@(self.targetReady),@"renderProbeMatched":@(renderProbe.ready),@"pointerNow":[self pointerSnapshot],@"lastGameplayPointer":self.lastGameplayPointer?:@{},@"secondsSinceGameplaySnapshot":@(self.lastGameplayDiagnostics?CACurrentMediaTime()-self.lastGameplayDiagnostics:-1),@"environment":settings,@"inputPollingIntervalSeconds":@(self.inputTimerInterval),@"recentGameplaySamples":self.performanceSamples,@"stallCapture":[self.stallCapture report],@"touch":self.touchView?[self.touchView diagnostics]:@{},@"enhancedTouch":@(self.enhancedTouch),@"touchIntegrationReady":@(self.touchIntegrationReady),@"display":[self.display diagnostics],@"displayMode":@(self.display.mode),@"displayHooksReady":@(self.displayHooksReady),@"displayRect":NSStringFromCGRect([self.display displayRectInView:self.touchPad])};
    NSData *json=[NSJSONSerialization dataWithJSONObject:report options:NSJSONWritingPrettyPrinted error:nil];
    return [[NSString alloc] initWithData:json encoding:NSUTF8StringEncoding]?:@"Unable to create diagnostics";
}
- (void)ensureOverlay {
    if (self.overlay) { if (self.overlay.hidden) self.overlay.hidden=NO; return; }
    UIWindowScene *scene=nil;
    for (UIScene *candidate in UIApplication.sharedApplication.connectedScenes)
        if ([candidate isKindOfClass:UIWindowScene.class] && candidate.activationState==UISceneActivationStateForegroundActive) { scene=(UIWindowScene *)candidate; break; }
    if (!scene) return;
    self.overlay=[[NXKWindow alloc] initWithWindowScene:scene];
    self.overlay.windowLevel=UIWindowLevelNormal+5;
    UIViewController *root=[NXKOverlayRoot new]; root.view.backgroundColor=UIColor.clearColor;
    self.overlay.rootViewController=root;
    self.button=[UIButton buttonWithType:UIButtonTypeSystem];
    self.button.frame=CGRectMake(0,0,124,38);
    self.button.layer.cornerRadius=19; self.button.titleLabel.font=[UIFont boldSystemFontOfSize:14];
    [self.button setTitleColor:UIColor.whiteColor forState:UIControlStateNormal];
    [self.button addTarget:self action:@selector(tap:) forControlEvents:UIControlEventTouchUpInside];
    UIPanGestureRecognizer *pan=[[UIPanGestureRecognizer alloc] initWithTarget:self action:@selector(pan:)];
    [self.button addGestureRecognizer:pan];
    UILongPressGestureRecognizer *hold=[[UILongPressGestureRecognizer alloc] initWithTarget:self action:@selector(hold:)];
    hold.minimumPressDuration=0.5; [self.button addGestureRecognizer:hold];
    [root.view addSubview:self.button];
    self.overlay.hidden=NO;
    [root.view layoutIfNeeded];
    CGPoint position=CGPointMake(CGRectGetMidX(root.view.bounds),CGRectGetMaxY(root.view.bounds)-root.view.safeAreaInsets.bottom-24);
    NSArray *saved=[self.prefs arrayForKey:@"position"];
    if (saved.count==2 && [saved[0] isKindOfClass:NSNumber.class] && [saved[1] isKindOfClass:NSNumber.class]) {
        double x=[saved[0] doubleValue],y=[saved[1] doubleValue];
        if (isfinite(x) && isfinite(y)) position=CGPointMake(x*root.view.bounds.size.width,y*root.view.bounds.size.height);
    }
    self.button.center=[self clamp:position];
    [self refresh];
}
- (CGPoint)clamp:(CGPoint)p {
    UIView *v=self.overlay.rootViewController.view;
    UIEdgeInsets safe=v.safeAreaInsets;
    return CGPointMake(MAX(safe.left+64,MIN(v.bounds.size.width-safe.right-64,p.x)),MAX(safe.top+21,MIN(v.bounds.size.height-safe.bottom-21,p.y)));
}
- (void)pan:(UIPanGestureRecognizer *)gesture {
    if (gesture.state==UIGestureRecognizerStateBegan) { self.dragOrigin=self.button.center; self.dragging=YES; }
    CGPoint t=[gesture translationInView:self.overlay.rootViewController.view];
    self.button.center=[self clamp:CGPointMake(self.dragOrigin.x+t.x,self.dragOrigin.y+t.y)];
    if (gesture.state==UIGestureRecognizerStateEnded || gesture.state==UIGestureRecognizerStateCancelled) {
        CGSize size=self.overlay.rootViewController.view.bounds.size;
        [self.prefs setObject:@[@(self.button.center.x/MAX(size.width,1)),@(self.button.center.y/MAX(size.height,1))] forKey:@"position"];
        self.dragging=NO;
    }
}
- (void)tap:(UIButton *)button { (void)button; if (!self.dragging && self.targetReady) [self setMappingEnabled:!self.enabled]; }
- (void)hold:(UILongPressGestureRecognizer *)gesture {
    if (gesture.state!=UIGestureRecognizerStateBegan || self.overlay.rootViewController.presentedViewController) return;
    [self clear]; self.suspended=YES;
    [self.overlay.rootViewController setNeedsStatusBarAppearanceUpdate];
    [self.overlay.rootViewController setNeedsUpdateOfHomeIndicatorAutoHidden];
    [self refresh];
    NXKSettings *settings=[[NXKSettings alloc] initWithStyle:UITableViewStyleInsetGrouped];
    settings.manager=self; self.settings=settings;
    UINavigationController *nav=[[UINavigationController alloc] initWithRootViewController:settings];
    nav.modalPresentationStyle=UIModalPresentationFullScreen;
    [self.overlay.rootViewController presentViewController:nav animated:YES completion:nil];
}
- (void)refresh {
    [self.stallCapture setEligible:self.targetReady && renderProbe.ready && !self.suspended && [self gameIsForeground]];
    [self updateTouchControls];
    self.display.settingsVisible=self.suspended;
    [self updateInputTimer];
    [self updatePointerPreference];
    if (!self.suspended && [self gameIsForeground]) self.gamePointerLocked=self.overlay.windowScene.pointerLockState.isLocked;
    NSString *title; UIColor *color;
    if (!self.targetReady) { title=@"版本不符"; color=UIColor.systemRedColor; }
    else if (![self slotAvailable]) { title=@"输入被占用"; color=UIColor.systemRedColor; }
    else if (!self.keyboard && !self.mouse) { title=self.enhancedTouch?@"触控 · 长按设置":@"长按设置"; color=UIColor.systemBlueColor; }
    else if (!self.enabled) { title=@"键鼠：关"; color=UIColor.systemGrayColor; }
    else if (self.suspended || ![self gameIsForeground]) { title=@"键鼠：暂停"; color=UIColor.systemOrangeColor; }
    else { title=self.mouseReleased?@"鼠标已释放":@"键鼠：开"; color=UIColor.systemGreenColor; }
    if (self.button && ![title isEqualToString:self.lastButtonTitle]) {
        self.lastButtonTitle=title;
        [self.button setTitle:title forState:UIControlStateNormal];
        self.button.backgroundColor=[color colorWithAlphaComponent:0.85];
    }
    UIView *root=self.overlay.rootViewController.view;
    if (self.button && !self.dragging && (!CGRectEqualToRect(self.lastOverlayBounds,root.bounds) || !UIEdgeInsetsEqualToEdgeInsets(self.lastOverlayInsets,root.safeAreaInsets))) {
        self.lastOverlayBounds=root.bounds; self.lastOverlayInsets=root.safeAreaInsets;
        self.button.center=[self clamp:self.button.center];
    }
}
- (NSString *)keyName:(unsigned)key {
    if (key==256) return @"鼠标左键";
    if (key==257) return @"鼠标右键";
    if (key==258) return @"鼠标中键";
    if (key>=259 && key<=263) return [NSString stringWithFormat:@"鼠标侧键 %u",key-258];
    if (key==NXK_WHEEL_UP) return @"滚轮向上";
    if (key==NXK_WHEEL_DOWN) return @"滚轮向下";
    if (key>=4 && key<=29) return [NSString stringWithFormat:@"%c",'A'+key-4];
    if (key>=30 && key<=38) return [NSString stringWithFormat:@"%u",key-29];
    if (key==39) return @"0";
    NSDictionary *names=@{@40:@"Enter",@41:@"Esc",@42:@"Backspace",@43:@"Tab",@44:@"Space",@45:@"−",@46:@"=",@47:@"[",@48:@"]",@49:@"\\",@51:@";",@52:@"'",@53:@"`",@54:@",",@55:@".",@56:@"/",@79:@"→",@80:@"←",@81:@"↓",@82:@"↑",@224:@"左 Ctrl",@225:@"左 Shift",@226:@"左 Alt",@227:@"左 ⌘",@228:@"右 Ctrl",@229:@"右 Shift",@230:@"右 Alt",@231:@"右 ⌘"};
    if (names[@(key)]) return names[@(key)];
    if (key>=58 && key<=69) return [NSString stringWithFormat:@"F%u",key-57];
    return [NSString stringWithFormat:@"键码 %u",key];
}
- (NSString *)bindingLabel:(NSInteger)action {
    NSMutableArray *names=[NSMutableArray array];
    for (unsigned k=0;k<3;k++) if (input.bindings[action].keys[k]) [names addObject:[self keyName:input.bindings[action].keys[k]]];
    return names.count?[names componentsJoinedByString:@" / "]:@"未绑定";
}
- (void)finishCapture {
    self.captureAction=-1;
    [self clear]; [self blockPhysicalKeys]; [self save];
    [self.captureAlert dismissViewControllerAnimated:YES completion:nil];
    self.captureAlert=nil;
    [self.settings.tableView reloadData];
}
- (void)bindCode:(unsigned)code action:(NSInteger)action {
    nxk_bind_code(&input,code,(unsigned)action);
}
- (void)capture:(NSInteger)action from:(UIViewController *)vc {
    if (!self.mouse) { [self captureKeyboard:action from:vc]; return; }
    UIAlertController *choice=[UIAlertController alertControllerWithTitle:NXKNames[action] message:@"分别设置键盘和鼠标绑定；修改一种输入会保留另一种。" preferredStyle:UIAlertControllerStyleAlert];
    [choice addAction:[UIAlertAction actionWithTitle:@"按键盘上的键" style:UIAlertActionStyleDefault handler:^(UIAlertAction *a) {
        (void)a; dispatch_async(dispatch_get_main_queue(),^{ [self captureKeyboard:action from:vc]; });
    }]];
    [choice addAction:[UIAlertAction actionWithTitle:@"选择鼠标按键 / 滚轮" style:UIAlertActionStyleDefault handler:^(UIAlertAction *a) {
        (void)a; dispatch_async(dispatch_get_main_queue(),^{ [self chooseMouseBinding:action from:vc]; });
    }]];
    [choice addAction:[UIAlertAction actionWithTitle:@"清除绑定" style:UIAlertActionStyleDestructive handler:^(UIAlertAction *a) {
        (void)a; memset(self->input.bindings[action].keys,0,sizeof(self->input.bindings[action].keys)); [self save]; [self.settings.tableView reloadData];
    }]];
    [choice addAction:[UIAlertAction actionWithTitle:@"恢复此项默认" style:UIAlertActionStyleDefault handler:^(UIAlertAction *a) {
        (void)a; NXKInput defaults; nxk_defaults(&defaults); self->input.bindings[action]=defaults.bindings[action]; [self save]; [self.settings.tableView reloadData];
    }]];
    [choice addAction:[UIAlertAction actionWithTitle:@"取消" style:UIAlertActionStyleCancel handler:nil]];
    [vc presentViewController:choice animated:YES completion:nil];
}
- (void)chooseMouseBinding:(NSInteger)action from:(UIViewController *)vc {
    UIAlertController *choice=[UIAlertController alertControllerWithTitle:@"选择鼠标输入" message:nil preferredStyle:UIAlertControllerStyleAlert];
    for (NSNumber *key in @[@256,@257,@258,@259,@260,@261,@262,@263,@264,@265])
        [choice addAction:[UIAlertAction actionWithTitle:[self keyName:key.unsignedIntValue] style:UIAlertActionStyleDefault handler:^(UIAlertAction *a) {
            (void)a; [self bindCode:key.unsignedIntValue action:action]; [self blockPhysicalKeys]; [self save]; [self.settings.tableView reloadData];
        }]];
    [choice addAction:[UIAlertAction actionWithTitle:@"取消" style:UIAlertActionStyleCancel handler:nil]];
    [vc presentViewController:choice animated:YES completion:nil];
}
- (void)captureKeyboard:(NSInteger)action from:(UIViewController *)vc {
    if (!self.keyboard) {
        UIAlertController *alert=[UIAlertController alertControllerWithTitle:@"请先连接蓝牙键盘" message:nil preferredStyle:UIAlertControllerStyleAlert];
        [alert addAction:[UIAlertAction actionWithTitle:@"好" style:UIAlertActionStyleDefault handler:nil]];
        [vc presentViewController:alert animated:YES completion:nil]; return;
    }
    [self clear]; self.captureAction=action;
    self.captureAlert=[UIAlertController alertControllerWithTitle:NXKNames[action] message:@"松开已有按键，再按想绑定的键盘键。Shift、Ctrl 等也可单独绑定；已有鼠标绑定会保留。" preferredStyle:UIAlertControllerStyleAlert];
    __weak NXKManager *weakSelf=self;
    [self.captureAlert addAction:[UIAlertAction actionWithTitle:@"清除绑定" style:UIAlertActionStyleDestructive handler:^(UIAlertAction *a) {
        (void)a; NXKManager *s=weakSelf; if (!s) return;
        memset(s->input.bindings[action].keys,0,sizeof(s->input.bindings[action].keys)); [s finishCapture];
    }]];
    [self.captureAlert addAction:[UIAlertAction actionWithTitle:@"恢复此项默认" style:UIAlertActionStyleDefault handler:^(UIAlertAction *a) {
        (void)a; NXKManager *s=weakSelf; if (!s) return; NXKInput defaults; nxk_defaults(&defaults);
        s->input.bindings[action]=defaults.bindings[action]; [s finishCapture];
    }]];
    [self.captureAlert addAction:[UIAlertAction actionWithTitle:@"取消" style:UIAlertActionStyleCancel handler:^(UIAlertAction *a) { (void)a; [weakSelf finishCapture]; }]];
    [vc presentViewController:self.captureAlert animated:YES completion:nil];
}
@end

@implementation NXKSettings
- (void)viewDidLoad {
    [super viewDidLoad]; self.title=@"显示与操作";
    self.navigationItem.rightBarButtonItem=[[UIBarButtonItem alloc] initWithTitle:@"完成" style:UIBarButtonItemStyleDone target:self action:@selector(done)];
}
- (void)closeAndCapture:(BOOL)capture {
    [self.manager clear];
    [self dismissViewControllerAnimated:YES completion:^{
        [self.manager blockPhysicalKeys]; self.manager.suspended=NO;
        [self.manager.overlay.rootViewController setNeedsStatusBarAppearanceUpdate];
        [self.manager.overlay.rootViewController setNeedsUpdateOfHomeIndicatorAutoHidden];
        [self.manager refresh];
        if (capture) [self.manager.stallCapture beginWithBase:self.manager.targetBase];
    }];
}
- (void)done { [self closeAndCapture:NO]; }
- (NSInteger)numberOfSectionsInTableView:(UITableView *)tableView { (void)tableView; return 6; }
- (NSInteger)tableView:(UITableView *)tableView numberOfRowsInSection:(NSInteger)section { (void)tableView; return section==5?1:(section==1?NXK_ACTION_COUNT:(section==0?9:(section==2?6:4))); }
- (NSString *)tableView:(UITableView *)tableView titleForHeaderInSection:(NSInteger)section { (void)tableView; return section==0?@"显示与触控":(section==1?@"点选动作后绑定键盘或鼠标":(section==2?@"鼠标":(section==3?@"状态":(section==4?@"快速恢复":@"构建资源")))); }
- (NSString *)tableView:(UITableView *)tableView titleForFooterInSection:(NSInteger)section {
    (void)tableView;
    if (section==1) return @"覆盖全部游戏内 Pro Controller 操作。未绑定项不会发送输入；同一键绑定多项时会同时触发。键盘和触屏均可使用。";
    if (section==2) return @"Esc 默认切换鼠标游戏模式；退出游戏模式后可以用系统指针操作菜单。快捷键可在上方改绑。系统可能不允许捕获指针，这里显示进入设置前的游戏状态。鼠标独立调整灵敏度。";
    if (section==3) return @"NXKeyboard 0.3.2 · 卡顿采集默认关闭。手动开启后最多运行 60 秒、记录 8 次；重新打开设置或进入后台即停止。采集会短暂读取线程状态，可能影响被采集的帧，仅用于排查。完成后复制诊断或导出 log.txt。";
    if (section==4) return @"恢复只调整本插件的显示和操作。正在使用的主程序等待修复继续保留。";
    if (section==5) return @"先显示所需文件数量和大小，再由你选择导出。只包含新版着色器和界面适配所需的游戏资源，不读取存档或密钥；不会自动上传。";
    return @"增强触控可单独使用，无需连接键鼠。左下移动，右侧空白滑动转视角；移动、视角与按键互不抢占。隐藏按键仅隐藏图形。完整铺满保留全部画面，会改变比例；可切换回原比例。";
}
- (UITableViewCell *)tableView:(UITableView *)tableView cellForRowAtIndexPath:(NSIndexPath *)index {
    (void)tableView; UITableViewCell *cell=[[UITableViewCell alloc] initWithStyle:UITableViewCellStyleValue1 reuseIdentifier:nil];
    NXKManager *m=self.manager;
    if (index.section==0) {
        if (index.row==0) {
            cell.textLabel.text=@"启用键鼠映射"; UISwitch *sw=[UISwitch new]; sw.on=m.enabled;
            [sw addTarget:self action:@selector(enabled:) forControlEvents:UIControlEventValueChanged]; cell.accessoryView=sw;
        } else if (index.row==1) {
            cell.textLabel.text=@"隐藏触控按键图形";
            UISwitch *sw=[UISwitch new]; sw.on=m.hideTouchControls;
            [sw addTarget:self action:@selector(hideControls:) forControlEvents:UIControlEventValueChanged]; cell.accessoryView=sw;
        } else if (index.row==2) { cell.textLabel.text=@"键盘视角速度"; cell.detailTextLabel.text=[NSString stringWithFormat:@"%d%%",m->input.camera_max*100/32767]; cell.accessoryType=UITableViewCellAccessoryDisclosureIndicator; }
        else if (index.row==3 || index.row==4 || index.row==5) {
            UISwitch *sw=[UISwitch new]; sw.tag=index.row;
            cell.textLabel.text=index.row==3?@"增强触控":(index.row==4?@"反转触屏视角 Y 轴":@"沉浸显示");
            sw.on=index.row==3?m.enhancedTouch:(index.row==4?m.invertTouchY:m.display.immersive);
            [sw addTarget:self action:@selector(touchOption:) forControlEvents:UIControlEventValueChanged]; cell.accessoryView=sw;
        } else {
            cell.accessoryType=UITableViewCellAccessoryDisclosureIndicator;
            if (index.row==6) { cell.textLabel.text=@"画面显示"; cell.detailTextLabel.text=@[@"原始比例",@"完整铺满",@"等比裁边"][m.display.mode]; }
            if (index.row==7) { cell.textLabel.text=@"触屏视角灵敏度"; cell.detailTextLabel.text=[NSString stringWithFormat:@"%.2g×",(double)m.touchSensitivity]; }
            if (index.row==8) { cell.textLabel.text=@"触控按键大小"; cell.detailTextLabel.text=[NSString stringWithFormat:@"%.0f%%",(double)m.touchScale*100]; }
        }
    } else if (index.section==1) {
        cell.textLabel.text=NXKNames[index.row]; cell.detailTextLabel.text=[m bindingLabel:index.row]; cell.accessoryType=UITableViewCellAccessoryDisclosureIndicator;
    } else if (index.section==2) {
        if (index.row==3) {
            cell.textLabel.text=@"鼠标灵敏度"; cell.detailTextLabel.text=[NSString stringWithFormat:@"%.2g×",m.mouseSensitivity];
            cell.accessoryType=UITableViewCellAccessoryDisclosureIndicator;
        } else if (index.row==5) {
            cell.textLabel.text=@"游戏内指针状态";
            cell.detailTextLabel.text=m.gamePointerLocked?@"已捕获":@"未捕获";
        } else {
            UISwitch *sw=[UISwitch new]; sw.tag=index.row;
            NSArray *titles=@[@"鼠标移动控制视角",@"捕获系统指针",@"反转鼠标 Y 轴",@"",@"鼠标游戏模式"];
            cell.textLabel.text=titles[index.row];
            sw.on=index.row==0?m.mouseLook:(index.row==1?m.lockPointer:(index.row==2?m.invertMouseY:!m.mouseReleased));
            [sw addTarget:self action:@selector(mouseOption:) forControlEvents:UIControlEventValueChanged]; cell.accessoryView=sw;
        }
    } else if (index.section==3) {
        if (index.row==1) {
            cell.textLabel.text=@"复制操作 / 性能诊断";
            cell.accessoryType=UITableViewCellAccessoryDisclosureIndicator;
        } else if (index.row==2) {
            cell.textLabel.text=@"采集卡顿（60 秒）";
            cell.accessoryType=UITableViewCellAccessoryDisclosureIndicator;
            cell.userInteractionEnabled=m.targetReady && m->renderProbe.ready;
            cell.textLabel.enabled=cell.userInteractionEnabled;
        } else if (index.row==3) {
            cell.textLabel.text=@"卡顿采集记录";
            cell.detailTextLabel.text=[NSString stringWithFormat:@"%lu 份 · %@",(unsigned long)m.stallCapture.samples.count,m.stallCapture.status];
        } else {
            cell.textLabel.text=m.targetReady?@"游戏版本已匹配":@"游戏版本不匹配";
            cell.detailTextLabel.text=!m.targetReady?@"禁止发送输入":(![m slotAvailable]?@"输入被占用":(m.mouse?(m.keyboard?@"键鼠已连接":@"鼠标已连接"):(m.keyboard?@"键盘已连接":(m.enhancedTouch?@"纯触控":@"原触控"))));
        }
    } else if (index.section==4) {
        cell.textLabel.text=@[@"清除当前触控状态",@"恢复推荐操作",@"恢复原显示和触控",@"触控适配状态"][index.row];
        if (index.row<3) cell.accessoryType=UITableViewCellAccessoryDisclosureIndicator;
        else cell.detailTextLabel.text=m.touchIntegrationReady?@"已匹配":@"未匹配";
    } else {
        cell.textLabel.text=@"导出构建所需资源";
        cell.detailTextLabel.text=m.resourceExporter.busy?@"处理中":@"先查看大小";
        cell.accessoryType=UITableViewCellAccessoryDisclosureIndicator;
        cell.userInteractionEnabled=!m.resourceExporter.busy;
    }
    return cell;
}
- (void)touchOption:(UISwitch *)sw {
    NXKManager *m=self.manager;
    [m clear];
    if (sw.tag==3) [m setEnhancedTouchEnabled:sw.on];
    else if (sw.tag==4) m.invertTouchY=sw.on;
    else if (sw.tag==5) m.display.immersive=sw.on;
    [m save]; [m updateDisplay]; [m refresh];
}
- (void)enabled:(UISwitch *)sw { [self.manager setMappingEnabled:sw.on]; }
- (void)hideControls:(UISwitch *)sw { [self.manager setTouchControlsHidden:sw.on]; }
- (void)mouseOption:(UISwitch *)sw {
    NXKManager *m=self.manager;
    if (sw.tag==0) m.mouseLook=sw.on;
    else if (sw.tag==1) m.lockPointer=sw.on;
    else if (sw.tag==2) m.invertMouseY=sw.on;
    else if (sw.tag==4) [m setMouseModeReleased:!sw.on];
    nxk_mouse_clear(&m->mouseMotion); [m save]; [m refresh]; [self.tableView reloadData];
}
- (void)showResourceError:(NSError *)error {
    UIAlertController *alert=[UIAlertController alertControllerWithTitle:@"资源导出未完成" message:error.localizedDescription preferredStyle:UIAlertControllerStyleAlert];
    [alert addAction:[UIAlertAction actionWithTitle:@"好" style:UIAlertActionStyleDefault handler:nil]];
    [self presentViewController:alert animated:YES completion:nil];
}
- (void)startResourceExport {
    NXKResourceExporter *exporter=self.manager.resourceExporter;
    UIAlertController *progress=[UIAlertController alertControllerWithTitle:@"正在打包资源" message:@"0%\n请保持游戏在前台。" preferredStyle:UIAlertControllerStyleAlert];
    [progress addAction:[UIAlertAction actionWithTitle:@"取消" style:UIAlertActionStyleCancel handler:^(UIAlertAction *action) { (void)action; [exporter cancel]; }]];
    [self presentViewController:progress animated:YES completion:nil];
    __weak UIAlertController *weakProgress=progress;
    NSTimer *timer=[NSTimer scheduledTimerWithTimeInterval:0.5 repeats:YES block:^(NSTimer *t) {
        (void)t; weakProgress.message=[NSString stringWithFormat:@"%.0f%%\n请保持游戏在前台。",exporter.progress*100];
    }];
    [exporter exportWithCompletion:^(NSURL *url,NSError *error) {
        [timer invalidate]; [self.tableView reloadData];
        void (^complete)(void)=^{
            if (error) { [self showResourceError:error]; return; }
            UIActivityViewController *share=[[UIActivityViewController alloc] initWithActivityItems:@[url] applicationActivities:nil];
            share.popoverPresentationController.sourceView=self.view;
            share.popoverPresentationController.sourceRect=CGRectMake(CGRectGetMidX(self.view.bounds),CGRectGetMidY(self.view.bounds),1,1);
            [self presentViewController:share animated:YES completion:nil];
        };
        if (self.presentedViewController==progress) [progress dismissViewControllerAnimated:YES completion:complete];
        else complete();
    }];
}
- (void)inspectResources {
    NXKResourceExporter *exporter=self.manager.resourceExporter;
    if (exporter.busy) return;
    self.navigationItem.prompt=@"正在检查资源大小…";
    [exporter inspectWithBundle:NSBundle.mainBundle completion:^(NSDictionary *summary,NSError *error) {
        self.navigationItem.prompt=nil; [self.tableView reloadData];
        if (error) { [self showResourceError:error]; return; }
        NSString *size=[NSByteCountFormatter stringFromByteCount:[summary[@"totalBytes"] longLongValue] countStyle:NSByteCountFormatterCountStyleFile];
        NSUInteger missing=[summary[@"missing"] count]+[summary[@"rejected"] count];
        NSString *message=[NSString stringWithFormat:@"共 %@ 个文件，未压缩大小 %@。压缩后大小取决于内容。\n%@\n导出完成后可保存到文件，再发送压缩包。",summary[@"fileCount"],size,missing?[NSString stringWithFormat:@"有 %lu 个文件未找到或未通过检查，清单会记录。",(unsigned long)missing]:@"所需资源均已找到。"];
        UIAlertController *alert=[UIAlertController alertControllerWithTitle:@"导出构建资源" message:message preferredStyle:UIAlertControllerStyleAlert];
        if ([summary[@"canExport"] boolValue]) [alert addAction:[UIAlertAction actionWithTitle:@"导出压缩包" style:UIAlertActionStyleDefault handler:^(UIAlertAction *a) { (void)a; [self startResourceExport]; }]];
        else {
            NSString *reason;
            if (![summary[@"fileCount"] unsignedIntegerValue]) reason=@"没有找到可导出的资源文件。";
            else if (![summary[@"withinLimit"] boolValue]) reason=@"本次导出超过 2 GB 或文件数上限，请把这里显示的大小反馈。";
            else {
                NSString *needed=[NSByteCountFormatter stringFromByteCount:[summary[@"requiredFreeBytes"] longLongValue] countStyle:NSByteCountFormatterCountStyleFile];
                reason=[NSString stringWithFormat:@"可用空间不足，需至少 %@ 空闲空间。",needed];
            }
            alert.message=[message stringByAppendingFormat:@"\n%@",reason];
        }
        [alert addAction:[UIAlertAction actionWithTitle:@"取消" style:UIAlertActionStyleCancel handler:nil]];
        [self presentViewController:alert animated:YES completion:nil];
    }];
}
- (void)tableView:(UITableView *)tableView didSelectRowAtIndexPath:(NSIndexPath *)index {
    [tableView deselectRowAtIndexPath:index animated:YES];
    if (index.section==5) { [self inspectResources]; return; }
    if (index.section==4 && index.row<3) {
        NXKManager *m=self.manager; [m clear]; [m resetNativeTouch];
        if (index.row==1) {
            m.touchScale=1; m.touchSensitivity=1; m.invertTouchY=NO;
            m.hideTouchControls=NO; m.display.mode=NXKDisplayStretch; m.display.immersive=YES;
            [m setEnhancedTouchEnabled:YES];
        } else if (index.row==2) {
            m.hideTouchControls=NO; m.display.mode=NXKDisplayOriginal; m.display.immersive=NO;
            [m setEnhancedTouchEnabled:NO];
        }
        [m save]; [m updateDisplay]; [m refresh]; [tableView reloadData]; return;
    }
    if (index.section==0 && index.row>=6) {
        NXKManager *m=self.manager;
        NSString *title=index.row==6?@"画面显示":(index.row==7?@"触屏视角灵敏度":@"触控按键大小");
        NSString *message=index.row==6?@"完整铺满：保留全部游戏画面及 UI，会有拉伸。原始比例：不变形，保留黑边。等比裁边：不变形，但可能裁到两侧 UI。":(index.row==7?@"拉弓时视角自动降低到所选灵敏度的 60%。":@"尺寸改变会清除正在按住的触控状态。");
        UIAlertController *choice=[UIAlertController alertControllerWithTitle:title message:message preferredStyle:UIAlertControllerStyleAlert];
        NSArray<NSNumber *> *values=index.row==6?@[@0,@1,@2]:(index.row==7?@[@0.5,@0.75,@1,@1.5,@2,@3]:@[@0.8,@1,@1.2]);
        for (NSNumber *value in values) {
            NSString *label=index.row==6?@[@"原始比例",@"完整铺满（默认）",@"等比裁边"][value.integerValue]:(index.row==7?[NSString stringWithFormat:@"%@×",value]:[NSString stringWithFormat:@"%.0f%%",value.doubleValue*100]);
            [choice addAction:[UIAlertAction actionWithTitle:label style:UIAlertActionStyleDefault handler:^(UIAlertAction *action) {
                (void)action; [m clear];
                if (index.row==6) m.display.mode=value.integerValue;
                else if (index.row==7) m.touchSensitivity=value.doubleValue;
                else m.touchScale=value.doubleValue;
                [m save]; [m updateDisplay]; [m refresh]; [tableView reloadData];
            }]];
        }
        [choice addAction:[UIAlertAction actionWithTitle:@"取消" style:UIAlertActionStyleCancel handler:nil]];
        [self presentViewController:choice animated:YES completion:nil]; return;
    }
    if (index.section==3 && index.row==1) {
        UIPasteboard.generalPasteboard.string=[self.manager diagnosticText];
        UIAlertController *alert=[UIAlertController alertControllerWithTitle:@"诊断已复制" message:@"可以回到聊天直接粘贴。先在游戏里操作约 20 秒，再复制，可包含捕获结果和性能记录。" preferredStyle:UIAlertControllerStyleAlert];
        [alert addAction:[UIAlertAction actionWithTitle:@"好" style:UIAlertActionStyleDefault handler:nil]];
        [self presentViewController:alert animated:YES completion:nil]; return;
    }
    if (index.section==3 && index.row==2) {
        if (self.manager.targetReady && self.manager->renderProbe.ready) [self closeAndCapture:YES];
        return;
    }
    if (index.section==1) { [self.manager capture:index.row from:self]; return; }
    if (index.section==2 && index.row==3) {
        UIAlertController *a=[UIAlertController alertControllerWithTitle:@"鼠标灵敏度" message:@"鼠标移动被转换为右摇杆输入；根据鼠标 DPI 和手感调整。" preferredStyle:UIAlertControllerStyleAlert];
        for (NSNumber *factor in @[@0.5,@1,@1.5,@2,@3]) [a addAction:[UIAlertAction actionWithTitle:[NSString stringWithFormat:@"%@×",factor] style:UIAlertActionStyleDefault handler:^(UIAlertAction *action) {
            (void)action; self.manager.mouseSensitivity=factor.doubleValue; [self.manager save]; [tableView reloadData];
        }]];
        [a addAction:[UIAlertAction actionWithTitle:@"取消" style:UIAlertActionStyleCancel handler:nil]];
        [self presentViewController:a animated:YES completion:nil]; return;
    }
    if (index.section==0 && index.row==2) {
        UIAlertController *a=[UIAlertController alertControllerWithTitle:@"视角速度" message:@"纯键盘转视角可先用 50%；精细瞄准也可以继续使用触屏。" preferredStyle:UIAlertControllerStyleAlert];
        for (NSNumber *percent in @[@25,@50,@75,@100]) [a addAction:[UIAlertAction actionWithTitle:[NSString stringWithFormat:@"%@%%",percent] style:UIAlertActionStyleDefault handler:^(UIAlertAction *action) {
            (void)action; self.manager->input.camera_max=32767*percent.intValue/100; [self.manager save]; [tableView reloadData];
        }]];
        [a addAction:[UIAlertAction actionWithTitle:@"取消" style:UIAlertActionStyleCancel handler:nil]];
        [self presentViewController:a animated:YES completion:nil];
    }
}
@end

__attribute__((constructor)) static void NXKLoad(void) {
    _dyld_register_func_for_remove_image(NXKImageRemoved);
    dispatch_async(dispatch_get_main_queue(),^{
        static NXKManager *manager;
        if (!manager) { manager=[NXKManager new]; [manager start]; }
    });
}
