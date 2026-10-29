#ifndef NXK_TOUCH_VIEW_H
#define NXK_TOUCH_VIEW_H

#import <UIKit/UIKit.h>
#import <QuartzCore/QuartzCore.h>
#include "NXKTouch.h"

typedef struct { int32_t id; float x,y; } NXKGameTouch;

/* A single transparent view owns finger routing. Its labels and shape layers
 * never intercept touches; a finger keeps its initial role through its end. */
@interface NXKTouchView : UIView
@property(nonatomic) BOOL active,controlsHidden,invertY;
@property(nonatomic) CGFloat controlScale,lookSensitivity;
@property(nonatomic,copy) void (^inputChanged)(void);
@property(nonatomic,copy) void (^cancelledButtons)(uint64_t buttons);
@property(nonatomic,copy) BOOL (^mapGamePoint)(CGPoint point,CGPoint *normalized);
@property(nonatomic,copy) void (^directTouchesChanged)(const NXKGameTouch *points,int count);
- (NXKOutput)outputAtTime:(double)now sample:(uint64_t)sample;
- (void)resetInput;
- (uint64_t)takeCancelledButtons;
- (NSDictionary *)diagnostics;
@end

@interface NXKTouchViewPoint : NSObject
@property(nonatomic,strong) UITouch *touch;
@property(nonatomic) uint64_t identifier;
@property(nonatomic) int32_t gameSlot;
@property(nonatomic) BOOL direct;
@property(nonatomic) CGPoint point;
@end
@implementation NXKTouchViewPoint
@end

@interface NXKTouchView () {
    NXKTouchState _touchState;
    CGRect _buttonRects[16];
    NXKGameTouch _lastDirect[16];
    int _lastDirectCount;
    uint64_t _nextIdentifier,_recoveries,_layoutResets,_pendingCancelledButtons;
    CGRect _previousBounds;
    UIEdgeInsets _previousInsets;
    CGPoint _stickHome;
    CGFloat _laidOutScale;
    NXKOutput _lastOutput;
    uint64_t _visualButtons;
    CGPoint _visualCenter,_visualKnob;
    CGFloat _visualRadius;
    BOOL _visualInitialized,_visualHidden;
}
@property(nonatomic,strong) NSMutableDictionary<NSValue *,NXKTouchViewPoint *> *tracked;
@property(nonatomic,strong) NSArray<UILabel *> *buttonLabels;
@property(nonatomic,strong) CAShapeLayer *stickRing,*stickKnob;
@property(nonatomic,strong) UIColor *idleColor,*pressedColor,*idleBorder,*pressedBorder;
- (void)updateVisuals;
- (void)publishDirectTouches;
- (void)finishTouches:(NSSet<UITouch *> *)touches;
- (BOOL)recoverEndedTouches;
@end

@implementation NXKTouchView
- (instancetype)initWithFrame:(CGRect)frame {
    if ((self=[super initWithFrame:frame])) {
        nxk_touch_init(&_touchState);
        _active=YES;
        _controlScale=1;
        _lookSensitivity=1;
        _previousBounds=CGRectNull;
        self.multipleTouchEnabled=YES;
        self.exclusiveTouch=NO;
        self.autoresizingMask=UIViewAutoresizingFlexibleWidth|UIViewAutoresizingFlexibleHeight;
        self.backgroundColor=UIColor.clearColor;
        self.opaque=NO;
        self.tracked=[NSMutableDictionary dictionary];
        self.idleColor=[UIColor colorWithWhite:0.05 alpha:0.38];
        self.pressedColor=[UIColor colorWithRed:0.12 green:0.56 blue:0.91 alpha:0.72];
        self.idleBorder=[UIColor colorWithWhite:1 alpha:0.45];
        self.pressedBorder=[UIColor colorWithWhite:1 alpha:0.95];
        NSArray *titles=@[@"A",@"B",@"X",@"Y",@"L3",@"R3",@"L",@"R",@"ZL",@"ZR",@"+",@"−",@"◀",@"▲",@"▶",@"▼"];
        NSMutableArray *labels=[NSMutableArray array];
        for (NSString *title in titles) {
            UILabel *label=[UILabel new];
            label.text=title;
            label.textAlignment=NSTextAlignmentCenter;
            label.textColor=UIColor.whiteColor;
            label.backgroundColor=self.idleColor;
            label.layer.borderWidth=1;
            label.layer.borderColor=self.idleBorder.CGColor;
            label.clipsToBounds=YES;
            label.userInteractionEnabled=NO;
            [self addSubview:label];
            [labels addObject:label];
        }
        self.buttonLabels=labels;
        self.stickRing=[CAShapeLayer layer];
        self.stickRing.fillColor=[UIColor colorWithWhite:0.08 alpha:0.22].CGColor;
        self.stickRing.strokeColor=[UIColor colorWithWhite:1 alpha:0.45].CGColor;
        self.stickRing.lineWidth=1.5;
        [self.layer insertSublayer:self.stickRing atIndex:0];
        self.stickKnob=[CAShapeLayer layer];
        self.stickKnob.fillColor=[UIColor colorWithWhite:1 alpha:0.3].CGColor;
        self.stickKnob.strokeColor=[UIColor colorWithWhite:1 alpha:0.65].CGColor;
        self.stickKnob.lineWidth=1;
        [self.layer insertSublayer:self.stickKnob above:self.stickRing];
    }
    return self;
}
- (void)setActive:(BOOL)active {
    if (_active==active) return;
    _active=active;
    if (!active) [self resetInput];
    self.hidden=!active;
    self.userInteractionEnabled=active;
    [self updateVisuals];
}
- (void)setControlsHidden:(BOOL)hidden {
    if (_controlsHidden==hidden) return;
    _controlsHidden=hidden;
    [self updateVisuals];
}
- (void)setControlScale:(CGFloat)scale {
    scale=isfinite(scale)?fmax(0.8,fmin(1.2,scale)):1;
    if (_controlScale==scale) return;
    _controlScale=scale;
    [self setNeedsLayout];
}
- (void)setLookSensitivity:(CGFloat)sensitivity {
    _lookSensitivity=isfinite(sensitivity)?fmax(0.25,fmin(3,sensitivity)):1;
}
- (void)setInvertY:(BOOL)invertY {
    if (_invertY==invertY) return;
    _invertY=invertY;
    nxk_touch_clear_look(&_touchState);
}
- (void)layoutSubviews {
    [super layoutSubviews];
    UIEdgeInsets insets=self.safeAreaInsets;
    BOOL changed=!CGRectEqualToRect(_previousBounds,self.bounds) ||
        !UIEdgeInsetsEqualToEdgeInsets(_previousInsets,insets) || _laidOutScale!=self.controlScale;
    if (!changed) return;
    BOOL hadInput=self.tracked.count>0 || _lastOutput.buttons || _lastOutput.axes;
    uint64_t cancelled=_lastOutput.buttons;
    [self resetInput];
    if (hadInput) _layoutResets++;
    _previousBounds=self.bounds;
    _previousInsets=insets;
    _laidOutScale=self.controlScale;
    CGRect safe=UIEdgeInsetsInsetRect(self.bounds,insets);
    CGFloat width=fmax(1,safe.size.width),height=fmax(1,safe.size.height);
    CGFloat size=fmax(44,56*self.controlScale);
    /* Retain a 44 point minimum even in compact windows. */
    size=fmax(44,fmin(size,fmin(width/9.5,height/5.1)));
    CGFloat gap=size*1.05;
    CGFloat left=CGRectGetMinX(safe)+10,right=CGRectGetMaxX(safe)-10;
    CGFloat top=CGRectGetMinY(safe)+10,bottom=CGRectGetMaxY(safe)-10;
    CGPoint positions[16]={0};
    CGPoint face=CGPointMake(right-size*1.55,bottom-size*1.75);
    positions[0]=CGPointMake(face.x+gap,face.y);
    positions[1]=CGPointMake(face.x,face.y+gap);
    positions[2]=CGPointMake(face.x,face.y-gap);
    positions[3]=CGPointMake(face.x-gap,face.y);
    positions[4]=CGPointMake(left+size*3.05,bottom-size*0.5);
    positions[5]=CGPointMake(right-size*3.05,bottom-size*0.5);
    positions[6]=CGPointMake(left+size*0.5,top+size*0.5);
    positions[8]=CGPointMake(left+size*1.75,top+size*0.5);
    positions[7]=CGPointMake(right-size*0.5,top+size*0.5);
    positions[9]=CGPointMake(right-size*1.75,top+size*0.5);
    positions[10]=CGPointMake(CGRectGetMidX(safe)+size*0.65,top+size*0.5);
    positions[11]=CGPointMake(CGRectGetMidX(safe)-size*0.65,top+size*0.5);
    CGPoint dpad=CGPointMake(left+size*1.55,top+size*2.65);
    positions[12]=CGPointMake(dpad.x-gap,dpad.y);
    positions[13]=CGPointMake(dpad.x,dpad.y-gap);
    positions[14]=CGPointMake(dpad.x+gap,dpad.y);
    positions[15]=CGPointMake(dpad.x,dpad.y+gap);
    for (unsigned i=0;i<16;i++) {
        CGPoint point=positions[i];
        point.x=fmax(CGRectGetMinX(safe)+size/2,fmin(CGRectGetMaxX(safe)-size/2,point.x));
        point.y=fmax(CGRectGetMinY(safe)+size/2,fmin(CGRectGetMaxY(safe)-size/2,point.y));
        _buttonRects[i]=CGRectMake(point.x-size/2,point.y-size/2,size,size);
        UILabel *label=self.buttonLabels[i];
        label.frame=_buttonRects[i];
        label.layer.cornerRadius=size*0.28;
        label.font=[UIFont systemFontOfSize:fmax(15,size*0.33) weight:UIFontWeightSemibold];
    }
    _touchState.radius=72*self.controlScale;
    _stickHome=CGPointMake(left+size*1.55,bottom-fmax(size*1.6,_touchState.radius+8));
    [self updateVisuals];
    if (hadInput && self.inputChanged) self.inputChanged();
    if (cancelled && self.cancelledButtons) self.cancelledButtons(cancelled);
}
- (void)safeAreaInsetsDidChange {
    [super safeAreaInsetsDidChange];
    [self setNeedsLayout];
}
- (void)updateVisuals {
    uint64_t buttons=0;
    const NXKTouchPoint *left=NULL;
    for (unsigned i=0;i<NXK_TOUCH_CAPACITY;i++) {
        const NXKTouchPoint *point=&_touchState.points[i];
        if (!point->id) continue;
        if (point->role==NXK_TOUCH_BUTTON) buttons|=point->buttons;
        else if (point->role==NXK_TOUCH_LEFT) left=point;
    }
    CGFloat radius=_touchState.radius;
    CGPoint center=left?CGPointMake(left->anchor_x,left->anchor_y):_stickHome;
    CGPoint knob=center;
    if (left) {
        double dx=left->x-center.x,dy=left->y-center.y,length=hypot(dx,dy);
        double factor=length>radius?radius/length:1;
        knob=CGPointMake(center.x+dx*factor,center.y+dy*factor);
    }
    BOOL visibilityChanged=!_visualInitialized || _visualHidden!=self.controlsHidden;
    BOOL buttonsChanged=!_visualInitialized || _visualButtons!=buttons;
    BOOL ringChanged=!_visualInitialized || _visualRadius!=radius || !CGPointEqualToPoint(_visualCenter,center);
    BOOL knobChanged=ringChanged || !CGPointEqualToPoint(_visualKnob,knob);
    if (!visibilityChanged && !buttonsChanged && !ringChanged && !knobChanged) return;
    [CATransaction begin];
    [CATransaction setDisableActions:YES];
    if (visibilityChanged || buttonsChanged) for (unsigned i=0;i<16;i++) {
        UILabel *label=self.buttonLabels[i];
        uint64_t mask=UINT64_C(1)<<i;
        if (visibilityChanged) label.hidden=self.controlsHidden;
        if (!_visualInitialized || ((_visualButtons^buttons)&mask)) {
            BOOL down=(buttons&mask)!=0;
            label.backgroundColor=down?self.pressedColor:self.idleColor;
            label.layer.borderColor=(down?self.pressedBorder:self.idleBorder).CGColor;
        }
    }
    if (ringChanged) self.stickRing.path=[UIBezierPath bezierPathWithOvalInRect:CGRectMake(center.x-radius,center.y-radius,radius*2,radius*2)].CGPath;
    if (knobChanged) {
        CGFloat knobRadius=radius*0.36;
        self.stickKnob.path=[UIBezierPath bezierPathWithOvalInRect:CGRectMake(knob.x-knobRadius,knob.y-knobRadius,knobRadius*2,knobRadius*2)].CGPath;
    }
    if (visibilityChanged) {
        self.stickRing.hidden=self.controlsHidden;
        self.stickKnob.hidden=self.controlsHidden;
    }
    [CATransaction commit];
    _visualInitialized=YES;
    _visualHidden=self.controlsHidden;
    _visualButtons=buttons;
    _visualCenter=center;
    _visualKnob=knob;
    _visualRadius=radius;
}
- (void)publishDirectTouches {
    NXKGameTouch points[16]={0};
    int count=0;
    NSMutableArray<NXKTouchViewPoint *> *direct=[NSMutableArray array];
    for (NXKTouchViewPoint *record in self.tracked.allValues) if (record.direct) [direct addObject:record];
    if (!direct.count && !_lastDirectCount) return;
    NSArray<NXKTouchViewPoint *> *records=[direct sortedArrayUsingComparator:^NSComparisonResult(NXKTouchViewPoint *a,NXKTouchViewPoint *b) {
        return a.identifier<b.identifier?NSOrderedAscending:(a.identifier>b.identifier?NSOrderedDescending:NSOrderedSame);
    }];
    for (NXKTouchViewPoint *record in records) {
        if (!record.direct || count>=16) continue;
        CGPoint normalized;
        if (!self.mapGamePoint || !self.mapGamePoint(record.point,&normalized) ||
            !isfinite(normalized.x) || !isfinite(normalized.y)) continue;
        points[count++]=(NXKGameTouch){record.gameSlot,(float)fmax(0,fmin(0.999999,normalized.x)),(float)fmax(0,fmin(0.999999,normalized.y))};
    }
    if (count==_lastDirectCount && !memcmp(points,_lastDirect,(size_t)count*sizeof(points[0]))) return;
    _lastDirectCount=count;
    memcpy(_lastDirect,points,(size_t)count*sizeof(points[0]));
    if (self.directTouchesChanged) self.directTouchesChanged(count?points:NULL,count);
}
- (BOOL)recoverEndedTouches {
    BOOL recovered=NO;
    for (NSValue *key in self.tracked.allKeys) {
        NXKTouchViewPoint *record=self.tracked[key];
        if (record.touch.phase!=UITouchPhaseEnded && record.touch.phase!=UITouchPhaseCancelled) continue;
        NXKTouchPoint *point=nxk_touch_find(&_touchState,record.identifier);
        if (record.touch.phase==UITouchPhaseCancelled && point && point->role==NXK_TOUCH_BUTTON)
            _pendingCancelledButtons|=point->buttons;
        nxk_touch_end(&_touchState,record.identifier);
        [self.tracked removeObjectForKey:key];
        _recoveries++;
        recovered=YES;
    }
    if (recovered) {
        [self publishDirectTouches];
        [self updateVisuals];
    }
    return recovered;
}
- (void)touchesBegan:(NSSet<UITouch *> *)touches withEvent:(UIEvent *)event {
    (void)event;
    if (!self.active) return;
    [self layoutIfNeeded];
    BOOL changed=[self recoverEndedTouches];
    for (UITouch *touch in touches) {
        if (touch.type!=UITouchTypeDirect && touch.type!=UITouchTypePencil) continue;
        NSValue *key=[NSValue valueWithNonretainedObject:touch];
        if (self.tracked[key] || self.tracked.count>=NXK_TOUCH_CAPACITY) continue;
        CGPoint point=[touch locationInView:self];
        if (!isfinite(point.x) || !isfinite(point.y)) continue;
        uint64_t buttons=0;
        for (unsigned i=0;i<16;i++) if (CGRectContainsPoint(_buttonRects[i],point)) { buttons=UINT64_C(1)<<i; break; }
        enum NXKTouchRole role=NXK_TOUCH_IGNORED;
        if (buttons) role=NXK_TOUCH_BUTTON;
        else if (point.x>=CGRectGetMidX(self.bounds)) role=NXK_TOUCH_LOOK;
        else if (point.y>=CGRectGetMinY(self.bounds)+self.bounds.size.height*0.42) role=NXK_TOUCH_LEFT;
        _nextIdentifier=(_nextIdentifier%INT32_MAX)+1;
        if (!nxk_touch_begin(&_touchState,_nextIdentifier,role,buttons,point.x,point.y,touch.timestamp)) continue;
        NXKTouchViewPoint *record=[NXKTouchViewPoint new];
        record.touch=touch;
        record.identifier=_nextIdentifier;
        record.point=point;
        record.direct=role==NXK_TOUCH_IGNORED;
        if (record.direct) {
            uint16_t used=0;
            for (NXKTouchViewPoint *existing in self.tracked.allValues)
                if (existing.direct) used|=(uint16_t)(1u<<existing.gameSlot);
            int32_t slot=0;
            while (slot<16 && (used&(1u<<slot))) slot++;
            /* At most sixteen records are admitted, so a direct slot exists. */
            if (slot==16) { nxk_touch_end(&_touchState,record.identifier); continue; }
            record.gameSlot=slot;
        }
        self.tracked[key]=record;
        changed=YES;
    }
    if (changed) {
        [self publishDirectTouches];
        [self updateVisuals];
        if (self.inputChanged) self.inputChanged();
    }
}
- (void)touchesMoved:(NSSet<UITouch *> *)touches withEvent:(UIEvent *)event {
    (void)event;
    if (!self.active) return;
    BOOL changed=[self recoverEndedTouches];
    for (UITouch *touch in touches) {
        NXKTouchViewPoint *record=self.tracked[[NSValue valueWithNonretainedObject:touch]];
        if (!record) continue;
        CGPoint point=[touch locationInView:self];
        if (!isfinite(point.x) || !isfinite(point.y)) continue;
        record.point=point;
        nxk_touch_move(&_touchState,record.identifier,point.x,point.y,touch.timestamp);
        changed=YES;
    }
    if (changed) {
        [self publishDirectTouches];
        [self updateVisuals];
        if (self.inputChanged) self.inputChanged();
    }
}
- (void)finishTouches:(NSSet<UITouch *> *)touches {
    BOOL changed=NO;
    for (UITouch *touch in touches) {
        NSValue *key=[NSValue valueWithNonretainedObject:touch];
        NXKTouchViewPoint *record=self.tracked[key];
        if (!record) continue;
        nxk_touch_end(&_touchState,record.identifier);
        [self.tracked removeObjectForKey:key];
        changed=YES;
    }
    if (changed) {
        [self publishDirectTouches];
        [self updateVisuals];
        if (self.inputChanged) self.inputChanged();
    }
}
- (void)touchesEnded:(NSSet<UITouch *> *)touches withEvent:(UIEvent *)event {
    (void)event;
    [self finishTouches:touches];
}
- (void)touchesCancelled:(NSSet<UITouch *> *)touches withEvent:(UIEvent *)event {
    (void)event;
    uint64_t cancelled=0;
    for (UITouch *touch in touches) {
        NXKTouchViewPoint *record=self.tracked[[NSValue valueWithNonretainedObject:touch]];
        NXKTouchPoint *point=record?nxk_touch_find(&_touchState,record.identifier):NULL;
        if (point && point->role==NXK_TOUCH_BUTTON) cancelled|=point->buttons;
    }
    [self finishTouches:touches];
    if (cancelled && self.cancelledButtons) self.cancelledButtons(cancelled);
}
- (NXKOutput)outputAtTime:(double)now sample:(uint64_t)sample {
    [self recoverEndedTouches];
    NXKOutput result={0,0};
    if (self.active) result=nxk_touch_output(&_touchState,now,sample,32*self.lookSensitivity,self.invertY);
    _lastOutput=result;
    return result;
}
- (uint64_t)takeCancelledButtons {
    uint64_t result=_pendingCancelledButtons; _pendingCancelledButtons=0; return result;
}
- (void)resetInput {
    _pendingCancelledButtons=0;
    if (!self.tracked.count && !_lastOutput.buttons && !_lastOutput.axes && !_lastDirectCount) return;
    nxk_touch_clear(&_touchState);
    [self.tracked removeAllObjects];
    _lastOutput=(NXKOutput){0,0};
    [self publishDirectTouches];
    [self updateVisuals];
}
- (NSDictionary *)diagnostics {
    unsigned left=0,look=0,buttons=0,direct=0;
    for (unsigned i=0;i<NXK_TOUCH_CAPACITY;i++) {
        const NXKTouchPoint *point=&_touchState.points[i];
        if (!point->id) continue;
        left+=point->role==NXK_TOUCH_LEFT;
        look+=point->role==NXK_TOUCH_LOOK;
        buttons+=point->role==NXK_TOUCH_BUTTON;
    }
    for (NXKTouchViewPoint *point in self.tracked.allValues) direct+=point.direct;
    return @{@"active":@(self.active),@"controlsHidden":@(self.controlsHidden),@"touches":@(self.tracked.count),@"leftTouches":@(left),@"lookTouches":@(look),@"buttonTouches":@(buttons),@"directTouches":@(direct),@"recoveries":@(_recoveries),@"layoutResets":@(_layoutResets),@"controlScale":@(self.controlScale),@"lookSensitivity":@(self.lookSensitivity),@"invertY":@(self.invertY),@"bounds":NSStringFromCGRect(self.bounds)};
}
@end

#endif
