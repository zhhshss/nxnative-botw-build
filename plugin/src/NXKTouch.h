#ifndef NXK_TOUCH_H
#define NXK_TOUCH_H
#include <math.h>
#include "NXKInput.h"

#define NXK_TOUCH_CAPACITY 16
#define NXK_TOUCH_BUTTON_MASK UINT64_C(0xffff)
enum NXKTouchRole {
    NXK_TOUCH_IGNORED = 0, NXK_TOUCH_LEFT, NXK_TOUCH_LOOK, NXK_TOUCH_BUTTON
};
typedef struct {
    uint64_t id, buttons;
    enum NXKTouchRole role;
    double anchor_x, anchor_y, x, y, time;
} NXKTouchPoint;
typedef struct {
    NXKTouchPoint points[NXK_TOUCH_CAPACITY];
    double radius, deadzone;
    double look_dx, look_dy, look_dt, look_event_time, look_until;
    double output_time;
    uint64_t look_sample;
    int32_t look_x, look_y;
    bool has_output_time;
} NXKTouchState;

/* Coordinates/radius are UIKit points; timestamps are monotonic seconds.
 * UIKit y increases downward, so the default stick y sign is reversed.
 * Only LEFT and LOOK have exclusive owners; each BUTTON finger contributes
 * independently. A displaced owner remains ignored until its own end event.
 */
static inline void nxk_touch_clear(NXKTouchState *s) {
    double radius=s->radius, deadzone=s->deadzone;
    memset(s,0,sizeof(*s));
    s->radius=radius; s->deadzone=deadzone;
}
static inline void nxk_touch_init(NXKTouchState *s) {
    memset(s,0,sizeof(*s)); s->radius=72; s->deadzone=0.08;
}
static inline void nxk_touch_clear_look(NXKTouchState *s) {
    s->look_dx=0; s->look_dy=0; s->look_dt=0;
    s->look_event_time=0; s->look_until=0;
    s->look_x=0; s->look_y=0; s->look_sample=0;
}
static inline NXKTouchPoint *nxk_touch_find(NXKTouchState *s,uint64_t id) {
    if (!id) return NULL;
    for (unsigned i=0;i<NXK_TOUCH_CAPACITY;i++)
        if (s->points[i].id==id) return &s->points[i];
    return NULL;
}
static inline bool nxk_touch_begin(NXKTouchState *s,uint64_t id,
        enum NXKTouchRole role,uint64_t buttons,double x,double y,double now) {
    if (!id || nxk_touch_find(s,id) || !isfinite(x) || !isfinite(y) ||
        !isfinite(now) || role<NXK_TOUCH_IGNORED || role>NXK_TOUCH_BUTTON) return false;
    NXKTouchPoint *point=NULL;
    for (unsigned i=0;i<NXK_TOUCH_CAPACITY;i++)
        if (!s->points[i].id) { point=&s->points[i]; break; }
    if (!point) return false;
    if (role==NXK_TOUCH_LEFT || role==NXK_TOUCH_LOOK) {
        for (unsigned i=0;i<NXK_TOUCH_CAPACITY;i++)
            if (s->points[i].id && s->points[i].role==role)
                s->points[i].role=NXK_TOUCH_IGNORED;
        if (role==NXK_TOUCH_LOOK) nxk_touch_clear_look(s);
    }
    *point=(NXKTouchPoint){id,buttons & NXK_TOUCH_BUTTON_MASK,role,x,y,x,y,now};
    return true;
}
static inline bool nxk_touch_end(NXKTouchState *s,uint64_t id) {
    NXKTouchPoint *point=nxk_touch_find(s,id);
    if (!point) return false;
    if (point->role==NXK_TOUCH_LOOK) nxk_touch_clear_look(s);
    memset(point,0,sizeof(*point));
    return true;
}
static inline bool nxk_touch_move(NXKTouchState *s,uint64_t id,
                                  double x,double y,double now) {
    NXKTouchPoint *point=nxk_touch_find(s,id);
    if (!point || point->role==NXK_TOUCH_IGNORED) return false;
    if (!isfinite(x) || !isfinite(y) || !isfinite(now)) {
        nxk_touch_end(s,id); return false;
    }
    if (now<point->time) {
        /* A clock discontinuity establishes a fresh baseline, never a delta. */
        if (point->role==NXK_TOUCH_LOOK) nxk_touch_clear_look(s);
        point->anchor_x=x; point->anchor_y=y;
        point->x=x; point->y=y; point->time=now;
        return false;
    }
    if (point->role==NXK_TOUCH_LOOK) {
        double dx=x-point->x,dy=y-point->y;
        if (!isfinite(dx) || !isfinite(dy)) {
            nxk_touch_end(s,id); return false;
        }
        if (dx!=0 || dy!=0) {
            /* Drop a previously unconsumed sample after a long event gap. */
            if (s->look_dt>0 && now-s->look_event_time>0.05) {
                s->look_dx=0; s->look_dy=0; s->look_dt=0;
            }
            s->look_dx=fmax(-10000,fmin(10000,s->look_dx+dx));
            s->look_dy=fmax(-10000,fmin(10000,s->look_dy+dy));
            s->look_dt+=fmax(0.004,fmin(0.1,now-point->time));
            s->look_event_time=now;
        }
    }
    point->x=x; point->y=y; point->time=now;
    return true;
}
static inline void nxk_touch_limit(double *x,double *y) {
    double length=hypot(*x,*y);
    if (!isfinite(length)) { *x=0; *y=0; }
    else if (length>32767) { *x*=32767/length; *y*=32767/length; }
}
static inline NXKOutput nxk_touch_output(NXKTouchState *s,double now,
                                        uint64_t sampled,double gain,bool invert_y) {
    NXKOutput output={0,0};
    if (!isfinite(now) || (s->has_output_time && now<s->output_time)) {
        nxk_touch_clear(s); return output;
    }
    s->output_time=now; s->has_output_time=true;
    double lx=0,ly=0;
    bool has_look=false;
    double radius=isfinite(s->radius) && s->radius>0?s->radius:72;
    double deadzone=isfinite(s->deadzone)?fmax(0,fmin(0.95,s->deadzone)):0.08;
    for (unsigned i=0;i<NXK_TOUCH_CAPACITY;i++) {
        NXKTouchPoint *point=&s->points[i];
        if (!point->id) continue;
        if (point->role==NXK_TOUCH_BUTTON) output.buttons|=point->buttons;
        else if (point->role==NXK_TOUCH_LOOK) has_look=true;
        else if (point->role==NXK_TOUCH_LEFT) {
            double x=point->x-point->anchor_x,y=point->anchor_y-point->y;
            double length=hypot(x,y);
            if (isfinite(length) && length>0) {
                double amplitude=fmax(0,fmin(1,(length/radius-deadzone)/(1-deadzone)));
                lx=x/length*amplitude*32767;
                ly=y/length*amplitude*32767;
            }
        }
    }
    if (!has_look || !isfinite(gain) || gain<=0) nxk_touch_clear_look(s);
    else if (s->look_dt>0) {
        if (now-s->look_event_time>=0.05 || now<s->look_event_time) {
            nxk_touch_clear_look(s);
        } else {
            double x=s->look_dx/s->look_dt*gain;
            double y=-s->look_dy/s->look_dt*gain*(invert_y?-1:1);
            nxk_touch_limit(&x,&y);
            s->look_x=(int32_t)x; s->look_y=(int32_t)y;
            s->look_sample=sampled; s->look_until=s->look_event_time+0.05;
            s->look_dx=0; s->look_dy=0; s->look_dt=0;
        }
    } else if (sampled!=s->look_sample || now>=s->look_until) {
        s->look_x=0; s->look_y=0;
    }
    output.axes=nxk_pack((int32_t)lx,(int32_t)ly,s->look_x,s->look_y);
    return output;
}
/* Existing keyboard/mouse output already applies its own ZR scaling. Only
 * touch-look is reduced here, using the combined button mask. Select a whole
 * stick rather than individual axes, then retain the circular stick range.
 */
static inline NXKOutput nxk_touch_merge(NXKOutput other,NXKOutput touch) {
    NXKOutput output={other.buttons | touch.buttons,0};
    int32_t axes[4];
    for (unsigned stick=0;stick<2;stick++) {
        unsigned shift=stick*32;
        double ox=(int16_t)(other.axes>>shift),oy=(int16_t)(other.axes>>(shift+16));
        double tx=(int16_t)(touch.axes>>shift),ty=(int16_t)(touch.axes>>(shift+16));
        if (stick==1 && (output.buttons & (UINT64_C(1)<<9))) { tx*=0.6; ty*=0.6; }
        if (tx*tx+ty*ty>ox*ox+oy*oy) { ox=tx; oy=ty; }
        nxk_touch_limit(&ox,&oy);
        axes[stick*2]=(int32_t)ox; axes[stick*2+1]=(int32_t)oy;
    }
    output.axes=nxk_pack(axes[0],axes[1],axes[2],axes[3]);
    return output;
}
#endif
