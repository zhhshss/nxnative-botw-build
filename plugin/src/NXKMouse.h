#ifndef NXK_MOUSE_H
#define NXK_MOUSE_H
#include <math.h>
#include <stdlib.h>
#include "NXKInput.h"
typedef struct {
    double dx,dy,lease_until;
    uint64_t sampled;
    int32_t x,y;
} NXKMouseMotion;
static inline void nxk_mouse_clear(NXKMouseMotion *m) { memset(m,0,sizeof(*m)); }
static inline void nxk_mouse_move(NXKMouseMotion *m,double x,double y) {
    if (!isfinite(x) || !isfinite(y)) return;
    m->dx=fmax(-10000,fmin(10000,m->dx+x));
    m->dy=fmax(-10000,fmin(10000,m->dy+y));
}
/* Convert raw deltas into stick velocity; retain one short movement until the guest samples it. */
static inline void nxk_mouse_tick(NXKMouseMotion *m,double dt,double now,uint64_t sampled,double gain,bool invert_y) {
    if (!isfinite(dt) || !isfinite(now) || !isfinite(gain) || gain<=0) { nxk_mouse_clear(m); return; }
    if (m->dx!=0 || m->dy!=0) {
        dt=fmax(0.004,fmin(0.1,dt));
        double x=m->dx/dt*gain,y=m->dy/dt*gain*(invert_y?-1:1);
        double length=hypot(x,y);
        if (length>32767) { x*=32767/length; y*=32767/length; }
        m->x=(int32_t)x; m->y=(int32_t)y;
        m->sampled=sampled; m->lease_until=now+0.05;
        m->dx=0; m->dy=0;
    } else if (sampled!=m->sampled || now>=m->lease_until) { m->x=0; m->y=0; }
}
static inline NXKOutput nxk_mouse_merge(const NXKInput *s,const NXKMouseMotion *m) {
    NXKOutput out=nxk_evaluate(s);
    int32_t rx=(int16_t)(out.axes>>32),ry=(int16_t)(out.axes>>48);
    int32_t mx=m->x,my=m->y;
    if (nxk_active(s,NXK_ZR)) { mx=mx*3/5; my=my*3/5; }
    if (abs(mx)>abs(rx)) rx=mx;
    if (abs(my)>abs(ry)) ry=my;
    out.axes=nxk_pack((int16_t)out.axes,(int16_t)(out.axes>>16),rx,ry);
    return out;
}
#endif
