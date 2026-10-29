#ifndef NXK_DISPLAY_H
#define NXK_DISPLAY_H

#import <UIKit/UIKit.h>
#import <QuartzCore/CAMetalLayer.h>
#import <objc/message.h>
#import <objc/runtime.h>
#import <dlfcn.h>
#include <math.h>
#include <stdint.h>

typedef NS_ENUM(NSInteger,NXKDisplayMode) {
    NXKDisplayOriginal=0,
    NXKDisplayStretch=1,
    NXKDisplayCrop=2
};

/* Main-thread owned. This changes only the final UIKit view transform. The
 * game's bounds, drawable size, rendering resolution and frame pacing stay
 * under the host's control. It cannot exit iPad windowing or guarantee that
 * the system's multitasking controls disappear. */
@interface NXKDisplayController : NSObject
@property(nonatomic) NXKDisplayMode mode;
@property(nonatomic) BOOL immersive,eligible,settingsVisible;
@property(nonatomic) uintptr_t targetBase;
@property(nonatomic,copy) BOOL (^permitsRoot)(UIViewController *root);
- (BOOL)installHooks;
- (void)refreshInScene:(UIWindowScene *)scene;
/* Restores owned changes and sets eligible=NO. */
- (void)restore;
/* Full image rectangle, including any part cropped by the root view. */
- (CGRect)displayRectInView:(UIView *)view;
/* Rejects points outside the visible image, then maps to the full image. */
- (BOOL)normalizedGamePoint:(CGPoint)point inView:(UIView *)view result:(CGPoint *)result;
- (NSDictionary *)diagnostics;
@end

@interface NXKDisplayState : NSObject
@property(nonatomic,weak) UIViewController *root;
@property(nonatomic,weak) UIView *game;
@property(nonatomic) CGAffineTransform transform;
@property(nonatomic) CGPoint center;
@property(nonatomic) CGRect bounds;
@property(nonatomic) CGRect rootBounds;
@property(nonatomic) UIEdgeInsets rootInsets;
@property(nonatomic) CGAffineTransform appliedTransform;
@property(nonatomic) CGPoint appliedCenter;
@property(nonatomic) NXKDisplayMode mode;
@property(nonatomic,weak) UIView *pad,*fps;
@property(nonatomic) CGRect padFrame,fpsFrame;
@property(nonatomic) BOOL hadPad,hadFPS;
@property(nonatomic) BOOL rootClips,applied;
@end
@implementation NXKDisplayState
@end

@interface NXKDisplayController ()
@property(nonatomic,weak) UIViewController *currentRoot;
@property(nonatomic,strong) NXKDisplayState *state;
@property(nonatomic) BOOL applying,needsRefresh,inLayout;
@property(nonatomic) uint64_t layoutCallbacks,layoutApplied,layoutStableSkips,layoutReentries;
- (BOOL)allowsRoot:(UIViewController *)root;
- (UIView *)gameForRoot:(UIViewController *)root;
- (void)beforeLayout:(UIViewController *)root;
- (void)afterLayout:(UIViewController *)root;
- (void)restoreState;
- (void)updateSystemAppearance;
- (void)configurationChanged;
- (BOOL)hasStableLayout:(UIViewController *)root;
@end

static __weak NXKDisplayController *NXKActiveDisplay;
static Class NXKDisplayHookClass;
static uintptr_t NXKDisplayHookBase;

static BOOL NXKDisplayMethodMatches(Method method,uintptr_t base) {
    if (!method || !base) return NO;
    Dl_info info={0};
    return dladdr((const void *)method_getImplementation(method),&info) &&
        (uintptr_t)info.dli_fbase==base;
}

static BOOL NXKDisplayFiniteRect(CGRect rect) {
    return !CGRectIsNull(rect) && !CGRectIsInfinite(rect) &&
        isfinite(rect.origin.x) && isfinite(rect.origin.y) &&
        isfinite(rect.size.width) && isfinite(rect.size.height) &&
        rect.size.width>0 && rect.size.height>0;
}

static UIView *NXKDisplayRootSubview(UIViewController *root,NSString *name) {
    SEL selector=NSSelectorFromString(name);
    if (![root respondsToSelector:selector]) return nil;
    id value=((id(*)(id,SEL))objc_msgSend)(root,selector);
    return [value isKindOfClass:UIView.class]?value:nil;
}

@implementation NXKDisplayController
- (instancetype)init {
    if ((self=[super init])) {
        _mode=NXKDisplayStretch;
        _immersive=YES;
        _needsRefresh=YES;
    }
    return self;
}
- (void)setMode:(NXKDisplayMode)mode {
    if (mode<NXKDisplayOriginal || mode>NXKDisplayCrop) mode=NXKDisplayOriginal;
    if (_mode==mode) return;
    _mode=mode;
    [self configurationChanged];
}
- (void)setImmersive:(BOOL)immersive {
    if (_immersive==immersive) return;
    _immersive=immersive;
    [self configurationChanged];
}
- (void)setSettingsVisible:(BOOL)visible {
    if (_settingsVisible==visible) return;
    _settingsVisible=visible;
    [self configurationChanged];
}
- (void)setTargetBase:(uintptr_t)base {
    if (_targetBase==base) return;
    [self restoreState];
    _targetBase=base;
    self.currentRoot=nil;
    self.needsRefresh=YES;
}
- (void)setEligible:(BOOL)eligible {
    if (_eligible==eligible) return;
    _eligible=eligible;
    if (!eligible) [self restoreState];
    [self configurationChanged];
}
- (void)configurationChanged {
    self.needsRefresh=YES;
    [self.currentRoot.viewIfLoaded setNeedsLayout];
    [self updateSystemAppearance];
}
- (void)updateSystemAppearance {
    UIViewController *root=self.currentRoot;
    [root setNeedsStatusBarAppearanceUpdate];
    [root setNeedsUpdateOfHomeIndicatorAutoHidden];
    [root setNeedsUpdateOfScreenEdgesDeferringSystemGestures];
}
- (BOOL)allowsRoot:(UIViewController *)root {
    return NSThread.isMainThread && self.eligible && self.targetBase &&
        self.targetBase==NXKDisplayHookBase && NXKDisplayHookClass &&
        [root isKindOfClass:NXKDisplayHookClass] &&
        (!self.permitsRoot || self.permitsRoot(root));
}
- (UIView *)gameForRoot:(UIViewController *)root {
    UIView *rootView=root.viewIfLoaded;
    SEL selector=NSSelectorFromString(@"game");
    if (!rootView || ![root respondsToSelector:selector]) return nil;
    id candidate=((id(*)(id,SEL))objc_msgSend)(root,selector);
    Class gameClass=NSClassFromString(@"DmGameView");
    if (!gameClass || ![candidate isKindOfClass:gameClass] ||
        ![candidate isKindOfClass:UIView.class]) return nil;
    UIView *game=candidate;
    if (game.superview!=rootView || ![game.layer isKindOfClass:CAMetalLayer.class]) return nil;
    return game;
}
- (BOOL)installHooks {
    if (!NSThread.isMainThread || !self.eligible || !self.targetBase) return NO;
    Class cls=NSClassFromString(@"DmRootVC");
    if (!cls) return NO;
    if (NXKDisplayHookClass) {
        if (cls!=NXKDisplayHookClass || self.targetBase!=NXKDisplayHookBase) return NO;
        if (NXKActiveDisplay!=self) [NXKActiveDisplay restore];
        NXKActiveDisplay=self;
        return YES;
    }
    SEL layout=@selector(viewDidLayoutSubviews);
    SEL status=@selector(prefersStatusBarHidden);
    SEL home=@selector(prefersHomeIndicatorAutoHidden);
    SEL edges=@selector(preferredScreenEdgesDeferringSystemGestures);
    Method layoutMethod=class_getInstanceMethod(cls,layout);
    Method statusMethod=class_getInstanceMethod(cls,status);
    Method homeMethod=class_getInstanceMethod(cls,home);
    Method edgesMethod=class_getInstanceMethod(cls,edges);
    /* Refuse a different host or a previous third-party implementation rather
     * than guessing at methods that only share the expected class name. */
    if (!NXKDisplayMethodMatches(layoutMethod,self.targetBase) ||
        !NXKDisplayMethodMatches(statusMethod,self.targetBase) ||
        !NXKDisplayMethodMatches(homeMethod,self.targetBase) ||
        !NXKDisplayMethodMatches(edgesMethod,self.targetBase)) return NO;
    IMP originalLayout=method_getImplementation(layoutMethod);
    IMP originalStatus=method_getImplementation(statusMethod);
    IMP originalHome=method_getImplementation(homeMethod);
    IMP originalEdges=method_getImplementation(edgesMethod);
    IMP layoutReplacement=imp_implementationWithBlock(^(UIViewController *root) {
        NXKDisplayController *controller=NXKActiveDisplay;
        controller.layoutCallbacks++;
        /* A nested callback must not execute the host's setFrame: while our
         * nonidentity transform is installed. One outer pass owns layout. */
        if (controller.inLayout || controller.applying) {
            controller.layoutReentries++;
            return;
        }
        BOOL allowed=[controller allowsRoot:root];
        if (allowed && [controller hasStableLayout:root]) {
            controller.layoutStableSkips++;
            return;
        }
        controller.inLayout=YES;
        @try {
            if (allowed) [controller beforeLayout:root];
            else if (controller.state.root==root) [controller restoreState];
            ((void(*)(id,SEL))originalLayout)(root,layout);
            if (allowed) [controller afterLayout:root];
        } @finally {
            controller.inLayout=NO;
        }
    });
    IMP statusReplacement=imp_implementationWithBlock(^BOOL(UIViewController *root) {
        NXKDisplayController *controller=NXKActiveDisplay;
        if ([controller allowsRoot:root]) {
            if (controller.settingsVisible || root.presentedViewController) return NO;
            return controller.immersive;
        }
        return ((BOOL(*)(id,SEL))originalStatus)(root,status);
    });
    IMP homeReplacement=imp_implementationWithBlock(^BOOL(UIViewController *root) {
        NXKDisplayController *controller=NXKActiveDisplay;
        if ([controller allowsRoot:root]) {
            if (controller.settingsVisible || root.presentedViewController) return NO;
            return controller.immersive;
        }
        return ((BOOL(*)(id,SEL))originalHome)(root,home);
    });
    IMP edgesReplacement=imp_implementationWithBlock(^UIRectEdge(UIViewController *root) {
        NXKDisplayController *controller=NXKActiveDisplay;
        if ([controller allowsRoot:root]) {
            if (controller.settingsVisible || root.presentedViewController) return UIRectEdgeNone;
            return controller.immersive?UIRectEdgeAll:UIRectEdgeNone;
        }
        return ((UIRectEdge(*)(id,SEL))originalEdges)(root,edges);
    });
    NXKDisplayHookClass=cls;
    NXKDisplayHookBase=self.targetBase;
    NXKActiveDisplay=self;
    class_replaceMethod(cls,layout,layoutReplacement,method_getTypeEncoding(layoutMethod));
    class_replaceMethod(cls,status,statusReplacement,method_getTypeEncoding(statusMethod));
    class_replaceMethod(cls,home,homeReplacement,method_getTypeEncoding(homeMethod));
    class_replaceMethod(cls,edges,edgesReplacement,method_getTypeEncoding(edgesMethod));
    return YES;
}
- (void)beforeLayout:(UIViewController *)root {
    if (self.applying) return;
    NXKDisplayState *state=self.state;
    if (state && state.root!=root) [self restoreState];
    else if (state.applied) {
        self.applying=YES;
        [UIView performWithoutAnimation:^{
            state.game.transform=state.transform;
            state.game.center=state.center;
        }];
        state.applied=NO;
        self.applying=NO;
    }
}
- (BOOL)hasStableLayout:(UIViewController *)root {
    NXKDisplayState *state=self.state;
    if (self.needsRefresh || !state.applied || state.root!=root || state.mode!=self.mode ||
        self.mode==NXKDisplayOriginal || state.game!=[self gameForRoot:root]) return NO;
    UIView *game=state.game,*view=root.viewIfLoaded;
    if (!CGRectEqualToRect(state.rootBounds,view.bounds) ||
        !UIEdgeInsetsEqualToEdgeInsets(state.rootInsets,view.safeAreaInsets) ||
        !CGRectEqualToRect(state.bounds,game.bounds) ||
        !CGPointEqualToPoint(state.appliedCenter,game.center) ||
        !CGAffineTransformEqualToTransform(state.appliedTransform,game.transform) ||
        !view.clipsToBounds) return NO;
    /* The verified host callback also positions its FPS label. Do not skip a
     * newly added/replaced label or controls after startup/host reconfiguration. */
    UIView *pad=NXKDisplayRootSubview(root,@"pad"),*fps=NXKDisplayRootSubview(root,@"fps");
    return state.hadPad==(pad!=nil) && state.hadFPS==(fps!=nil) &&
        state.pad==pad && state.fps==fps &&
        (!pad || CGRectEqualToRect(state.padFrame,pad.frame)) &&
        (!fps || CGRectEqualToRect(state.fpsFrame,fps.frame));
}
- (void)afterLayout:(UIViewController *)root {
    if (self.applying || ![self allowsRoot:root]) return;
    UIView *game=[self gameForRoot:root];
    if (!game || !NXKDisplayFiniteRect(game.bounds) ||
        !NXKDisplayFiniteRect(root.view.bounds)) {
        [self restoreState];
        return;
    }
    self.currentRoot=root;
    if (self.mode==NXKDisplayOriginal) {
        /* The original layout has already supplied the new frame. Restore
         * only clipping here, without reinstating a stale pre-rotation frame. */
        if (self.state) root.view.clipsToBounds=self.state.rootClips;
        self.state=nil;
        self.needsRefresh=NO;
        return;
    }
    if (!CGAffineTransformIsIdentity(game.transform)) {
        [self restoreState];
        return;
    }
    if (self.state.game!=game) [self restoreState];
    NXKDisplayState *state=self.state;
    if (!state) {
        state=[NXKDisplayState new];
        state.root=root;
        state.game=game;
        state.rootClips=root.view.clipsToBounds;
        self.state=state;
    }
    state.transform=game.transform;
    state.center=game.center;
    state.bounds=game.bounds;
    state.rootBounds=root.view.bounds;
    state.rootInsets=root.view.safeAreaInsets;
    state.mode=self.mode;
    state.pad=NXKDisplayRootSubview(root,@"pad");
    state.fps=NXKDisplayRootSubview(root,@"fps");
    state.hadPad=state.pad!=nil;
    state.hadFPS=state.fps!=nil;
    state.padFrame=state.pad.frame;
    state.fpsFrame=state.fps.frame;
    CGRect viewport=root.view.bounds;
    CGFloat sx=viewport.size.width/game.bounds.size.width;
    CGFloat sy=viewport.size.height/game.bounds.size.height;
    if (self.mode==NXKDisplayCrop) sx=sy=fmax(sx,sy);
    if (!isfinite(sx) || !isfinite(sy) || sx<=0 || sy<=0) return;
    self.applying=YES;
    [UIView performWithoutAnimation:^{
        root.view.clipsToBounds=YES;
        game.center=CGPointMake(CGRectGetMidX(viewport),CGRectGetMidY(viewport));
        game.transform=CGAffineTransformMakeScale(sx,sy);
    }];
    state.applied=YES;
    state.appliedTransform=game.transform;
    state.appliedCenter=game.center;
    self.layoutApplied++;
    self.applying=NO;
    self.needsRefresh=NO;
}
- (void)restoreState {
    NXKDisplayState *state=self.state;
    self.state=nil;
    if (!state) return;
    self.applying=YES;
    [UIView performWithoutAnimation:^{
        if (state.applied) {
            state.game.transform=state.transform;
            state.game.bounds=state.bounds;
            state.game.center=state.center;
        }
        state.root.viewIfLoaded.clipsToBounds=state.rootClips;
    }];
    self.applying=NO;
}
- (void)restore {
    _eligible=NO;
    [self restoreState];
    [self updateSystemAppearance];
    [self.currentRoot.viewIfLoaded setNeedsLayout];
    self.currentRoot=nil;
    self.needsRefresh=YES;
}
- (void)refreshInScene:(UIWindowScene *)scene {
    if (!NSThread.isMainThread) return;
    if (!self.eligible || !scene || ![self installHooks]) {
        [self restoreState];
        return;
    }
    UIViewController *matched=nil;
    for (UIWindow *window in scene.windows) {
        if (window.hidden || window.alpha<=0 || ![self allowsRoot:window.rootViewController]) continue;
        if ([self gameForRoot:window.rootViewController]) {
            matched=window.rootViewController;
            break;
        }
    }
    if (!matched) {
        [self restoreState];
        self.currentRoot=nil;
        return;
    }
    if (self.currentRoot!=matched) {
        [self restoreState];
        self.currentRoot=matched;
        self.needsRefresh=YES;
        [self updateSystemAppearance];
    }
    if (self.needsRefresh) {
        [matched.view setNeedsLayout];
        [matched.view layoutIfNeeded];
        /* Some host versions finish UIView layout without scheduling the
         * controller callback. Reuse its verified method once if necessary. */
        if (self.needsRefresh) [matched viewDidLayoutSubviews];
    }
}
- (CGRect)displayRectInView:(UIView *)view {
    UIViewController *root=self.currentRoot;
    if (![self allowsRoot:root] || !view || !view.window || view.window!=root.viewIfLoaded.window) return CGRectNull;
    UIView *game=[self gameForRoot:root];
    if (!game) return CGRectNull;
    CGRect rect=[game convertRect:game.bounds toView:view];
    return NXKDisplayFiniteRect(rect)?rect:CGRectNull;
}
- (BOOL)normalizedGamePoint:(CGPoint)point inView:(UIView *)view result:(CGPoint *)result {
    if (!result || !isfinite(point.x) || !isfinite(point.y)) return NO;
    CGRect image=[self displayRectInView:view];
    if (!NXKDisplayFiniteRect(image)) return NO;
    UIView *root=self.currentRoot.viewIfLoaded;
    CGRect visible=CGRectIntersection(image,[root convertRect:root.bounds toView:view]);
    if (CGRectIsNull(visible) || !CGRectContainsPoint(visible,point)) return NO;
    *result=CGPointMake(fmax(0,fmin(1,(point.x-image.origin.x)/image.size.width)),
        fmax(0,fmin(1,(point.y-image.origin.y)/image.size.height)));
    return YES;
}
- (NSDictionary *)diagnostics {
    return @{@"eligible":@(self.eligible),@"mode":@(self.mode),@"immersive":@(self.immersive),
        @"applied":@(self.state.applied),@"layoutCallbacks":@(self.layoutCallbacks),
        @"layoutApplied":@(self.layoutApplied),@"layoutStableSkips":@(self.layoutStableSkips),
        @"layoutReentries":@(self.layoutReentries),@"rootBounds":NSStringFromCGRect(self.currentRoot.viewIfLoaded.bounds)};
}
@end

#endif
