#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include "../src/NXKTouch.h"
#include "../src/NXKWriter.h"

static int axis(NXKOutput o,unsigned n) { return (int16_t)(o.axes>>(16*n)); }
static NXKOutput output(NXKTouchState *s,double now,uint64_t sampled) {
    return nxk_touch_output(s,now,sampled,32,false);
}
static void circular(NXKOutput o,unsigned stick) {
    double x=axis(o,stick*2),y=axis(o,stick*2+1);
    assert(hypot(x,y)<=32767.001);
}
static void simultaneous_and_release(void) {
    NXKTouchState s; nxk_touch_init(&s);
    assert(nxk_touch_begin(&s,1,NXK_TOUCH_LEFT,0,100,200,1));
    assert(nxk_touch_begin(&s,2,NXK_TOUCH_LOOK,0,500,200,1));
    assert(nxk_touch_begin(&s,3,NXK_TOUCH_BUTTON,8,700,200,1));
    nxk_touch_move(&s,1,172,200,1.02);
    nxk_touch_move(&s,2,510,195,1.02);
    NXKOutput o=output(&s,1.02,10);
    assert(axis(o,0)==32767 && axis(o,1)==0 && o.buttons==8);
    assert(abs(axis(o,2)-16000)<=1 && abs(axis(o,3)-8000)<=1);
    nxk_touch_end(&s,2);
    o=output(&s,1.03,10);
    assert(axis(o,2)==0 && axis(o,3)==0 && axis(o,0)==32767 && o.buttons==8);
    nxk_touch_end(&s,1); o=output(&s,1.04,11);
    assert(o.axes==0 && o.buttons==8);
    nxk_touch_end(&s,3); assert(output(&s,1.05,12).buttons==0);
}
static void multi_finger_buttons_and_latch(void) {
    NXKTouchState s; nxk_touch_init(&s);
    _Atomic uint64_t buttons=0,latch=0,axes=0;
    NXKWriter writer={&buttons,&latch,&axes};
    nxk_touch_begin(&s,1,NXK_TOUCH_BUTTON,4,0,0,1);
    nxk_touch_begin(&s,2,NXK_TOUCH_BUTTON,4,0,0,1);
    nxk_touch_begin(&s,3,NXK_TOUCH_BUTTON,8,0,0,1);
    nxk_publish(&writer,output(&s,1,1));
    assert(atomic_exchange(&latch,0)==12);
    nxk_touch_end(&s,1); nxk_publish(&writer,output(&s,1.01,1));
    assert(atomic_load(&buttons)==12 && atomic_exchange(&latch,0)==0);
    nxk_touch_end(&s,2); nxk_publish(&writer,output(&s,1.02,1));
    assert(atomic_load(&buttons)==8);
    nxk_touch_end(&s,3); nxk_publish(&writer,output(&s,1.03,1));
    nxk_touch_begin(&s,4,NXK_TOUCH_BUTTON,2,0,0,1.04);
    nxk_publish(&writer,output(&s,1.04,1));
    nxk_touch_end(&s,4); nxk_publish(&writer,output(&s,1.041,1));
    assert(atomic_load(&buttons)==0 && atomic_exchange(&latch,0)==2);
    /* Touch release cannot release the same action still held on keyboard. */
    nxk_touch_begin(&s,5,NXK_TOUCH_BUTTON,8,0,0,1.05);
    nxk_publish(&writer,nxk_touch_merge((NXKOutput){8,0},output(&s,1.05,1)));
    assert(atomic_exchange(&latch,0)==8);
    nxk_touch_end(&s,5);
    nxk_publish(&writer,nxk_touch_merge((NXKOutput){8,0},output(&s,1.06,1)));
    assert(atomic_load(&buttons)==8 && atomic_exchange(&latch,0)==0);
}
static void cancellation_latch_merge(void) {
    _Atomic uint64_t buttons=0,latch=0,axes=0;
    NXKWriter writer={&buttons,&latch,&axes};
    nxk_publish(&writer,(NXKOutput){4,0});
    NXKOutput remaining=nxk_touch_merge((NXKOutput){0,0},(NXKOutput){0,0});
    nxk_publish(&writer,remaining);
    atomic_fetch_and(writer.latch,~(UINT64_C(4) & ~remaining.buttons));
    assert(atomic_load(&buttons)==0 && atomic_load(&latch)==0);
    nxk_publish(&writer,(NXKOutput){4,0});
    /* One source cancelled while the same action is held by another source. */
    remaining=nxk_touch_merge((NXKOutput){4,0},(NXKOutput){0,0});
    nxk_publish(&writer,remaining);
    atomic_fetch_and(writer.latch,~(UINT64_C(4) & ~remaining.buttons));
    assert(atomic_load(&buttons)==4 && atomic_load(&latch)==4);
    nxk_neutral(&writer);
    assert(atomic_load(&buttons)==0 && atomic_load(&latch)==0 && atomic_load(&axes)==0);
}
static void owners_and_fixed_roles(void) {
    NXKTouchState s; nxk_touch_init(&s);
    nxk_touch_begin(&s,1,NXK_TOUCH_LEFT,0,0,0,1);
    nxk_touch_move(&s,1,72,0,1.01);
    assert(axis(output(&s,1.01,1),0)==32767);
    nxk_touch_begin(&s,2,NXK_TOUCH_LEFT,0,500,500,1.02);
    assert(output(&s,1.02,1).axes==0);
    assert(!nxk_touch_move(&s,1,-500,500,1.03));
    nxk_touch_move(&s,2,500,428,1.03);
    assert(axis(output(&s,1.03,1),1)==32767);
    nxk_touch_end(&s,1);
    assert(axis(output(&s,1.04,1),1)==32767);
    nxk_touch_begin(&s,3,NXK_TOUCH_BUTTON,1,0,0,1.05);
    nxk_touch_move(&s,3,1000,1000,1.06);
    assert(output(&s,1.06,1).buttons==1);
    assert(!nxk_touch_begin(&s,3,NXK_TOUCH_LOOK,0,1000,1000,1.06));
    nxk_touch_begin(&s,4,NXK_TOUCH_LOOK,0,0,0,1.07);
    nxk_touch_move(&s,4,10,0,1.09);
    assert(axis(output(&s,1.09,1),2)>0);
    nxk_touch_begin(&s,5,NXK_TOUCH_LOOK,0,0,0,1.10);
    assert(axis(output(&s,1.10,1),2)==0);
    assert(!nxk_touch_move(&s,4,100,0,1.11));
    nxk_touch_end(&s,4);
    nxk_touch_move(&s,5,0,-10,1.12);
    assert(axis(output(&s,1.12,1),3)>0);
    assert(!nxk_touch_move(&s,999,0,0,1.13));
    assert(!nxk_touch_end(&s,999));
}
static void look_sampling_timeout_and_reset(void) {
    NXKTouchState s; nxk_touch_init(&s);
    nxk_touch_begin(&s,1,NXK_TOUCH_LOOK,0,0,0,1);
    nxk_touch_move(&s,1,10,-5,1.02);
    NXKOutput o=output(&s,1.02,10);
    assert(o.axes!=0 && output(&s,1.03,10).axes==o.axes);
    assert(output(&s,1.04,11).axes==0);
    nxk_touch_move(&s,1,20,-10,1.05);
    assert(output(&s,1.05,11).axes!=0);
    assert(output(&s,1.101,11).axes==0); /* Even if the guest stops sampling. */
    nxk_touch_move(&s,1,30,-15,1.12);
    assert(output(&s,1.18,11).axes==0); /* Do not replay an old queued delta. */
    nxk_touch_move(&s,1,40,-20,1.20);
    o=nxk_touch_output(&s,1.20,12,32,true);
    assert(axis(o,2)>0 && axis(o,3)<0);
    s.radius=90; s.deadzone=0.12;
    nxk_touch_clear(&s);
    assert(s.radius==90 && s.deadzone==0.12 && output(&s,2,13).axes==0);
    assert(!nxk_touch_move(&s,1,999,999,2.01));
    assert(!nxk_touch_end(&s,1));
    nxk_touch_begin(&s,2,NXK_TOUCH_LOOK,0,0,0,2.02);
    nxk_touch_move(&s,2,10,0,2.04);
    assert(output(&s,2.04,13).axes!=0);
    assert(output(&s,2.03,13).axes==0); /* Clock rollback cancels all owners. */
    assert(!nxk_touch_move(&s,2,20,0,2.05));
}
static void analog_bounds_and_velocity(void) {
    NXKTouchState s; nxk_touch_init(&s);
    nxk_touch_begin(&s,1,NXK_TOUCH_LEFT,0,0,0,1);
    nxk_touch_move(&s,1,4,0,1.01); assert(output(&s,1.01,1).axes==0);
    nxk_touch_move(&s,1,36,0,1.02);
    int half=axis(output(&s,1.02,1),0); assert(half>14000 && half<16000);
    nxk_touch_move(&s,1,720,-720,1.03);
    NXKOutput o=output(&s,1.03,1); circular(o,0);
    assert(axis(o,0)==axis(o,1) && axis(o,0)>23000);
    NXKTouchState slow,fast; nxk_touch_init(&slow); nxk_touch_init(&fast);
    nxk_touch_begin(&slow,1,NXK_TOUCH_LOOK,0,0,0,1);
    nxk_touch_begin(&fast,1,NXK_TOUCH_LOOK,0,0,0,1);
    nxk_touch_move(&slow,1,10,0,1.04);
    nxk_touch_move(&fast,1,5,0,1.02);
    int sx=axis(output(&slow,1.04,1),2),fx=axis(output(&fast,1.02,1),2);
    assert(abs(sx-fx)<=1);
    nxk_touch_clear(&fast); nxk_touch_begin(&fast,1,NXK_TOUCH_LOOK,0,0,0,2);
    nxk_touch_move(&fast,1,5,0,2.01); nxk_touch_move(&fast,1,10,0,2.02);
    assert(abs(axis(output(&fast,2.02,1),2)-16000)<=1);
    nxk_touch_move(&fast,1,100000,-100000,2.03);
    o=output(&fast,2.03,1); circular(o,1);
    assert(axis(o,2)>23000 && axis(o,3)>23000);
}
static void invalid_events_and_capacity(void) {
    NXKTouchState s; nxk_touch_init(&s);
    assert(!nxk_touch_begin(&s,0,NXK_TOUCH_BUTTON,1,0,0,1));
    assert(!nxk_touch_begin(&s,1,NXK_TOUCH_LOOK,0,NAN,0,1));
    assert(!nxk_touch_begin(&s,1,NXK_TOUCH_LOOK,0,0,0,INFINITY));
    nxk_touch_begin(&s,1,NXK_TOUCH_LOOK,0,0,0,1);
    assert(!nxk_touch_move(&s,1,INFINITY,0,1.01));
    assert(!nxk_touch_move(&s,1,10,0,1.02));
    nxk_touch_begin(&s,2,NXK_TOUCH_LEFT,0,0,0,2);
    nxk_touch_move(&s,2,72,0,2.01);
    assert(!nxk_touch_move(&s,2,100,100,1.99));
    assert(output(&s,2.02,1).axes==0); /* Move rollback resets its anchor. */
    nxk_touch_clear(&s);
    for (unsigned i=0;i<NXK_TOUCH_CAPACITY;i++)
        assert(nxk_touch_begin(&s,i+1,NXK_TOUCH_BUTTON,1,0,0,3));
    assert(!nxk_touch_begin(&s,99,NXK_TOUCH_LOOK,0,0,0,3));
    assert(output(&s,3,1).buttons==1);
    assert(output(&s,NAN,1).buttons==0);
    assert(nxk_touch_begin(&s,99,NXK_TOUCH_LOOK,0,0,0,4));
    nxk_touch_move(&s,99,10,10,4.02);
    assert(nxk_touch_output(&s,4.02,1,NAN,false).axes==0);
}
static void merging(void) {
    NXKOutput keyboard={1,nxk_pack(20000,0,16000,0)};
    NXKOutput touch={8,nxk_pack(0,25000,0,-20000)};
    NXKOutput o=nxk_touch_merge(keyboard,touch);
    assert(o.buttons==9 && axis(o,0)==0 && axis(o,1)==25000);
    assert(axis(o,2)==0 && axis(o,3)==-20000); /* No synthetic diagonal. */
    o=nxk_touch_merge((NXKOutput){512,0},touch);
    assert(o.buttons==520 && axis(o,3)==-12000);
    o=nxk_touch_merge(keyboard,(NXKOutput){512,nxk_pack(0,0,0,30000)});
    assert(o.buttons==513 && axis(o,2)==0 && axis(o,3)==18000);
    o=nxk_touch_merge((NXKOutput){0,nxk_pack(30000,30000,-32768,-32768)},(NXKOutput){0,0});
    circular(o,0); circular(o,1);
    assert(axis(o,0)>23000 && axis(o,2)<-23000);
    o=nxk_touch_merge(keyboard,(NXKOutput){0,nxk_pack(-20000,0,-16000,0)});
    assert(axis(o,0)==20000 && axis(o,2)==16000); /* Ties keep existing source. */
}
int main(void) {
    simultaneous_and_release(); multi_finger_buttons_and_latch();
    owners_and_fixed_roles(); look_sampling_timeout_and_reset(); cancellation_latch_merge();
    analog_bounds_and_velocity(); invalid_events_and_capacity(); merging();
    puts("Touch checks passed: concurrent controls, ownership, cancellation, multi-finger/latch, velocity, bounds, invalid input and source merging.");
    return 0;
}
