#include <assert.h>
#include <stdio.h>
#include "../src/NXKInput.h"
#include "../src/NXKWriter.h"
#include "../src/NXKTarget.h"
#include "../src/NXKMouse.h"
#include "../src/NXKPointerPolicy.h"
static int16_t axis(uint64_t packed,unsigned n) { return (int16_t)(packed>>(16*n)); }
int main(void) {
    NXKInput s; nxk_defaults(&s);
    NXKOutput out=nxk_evaluate(&s); assert(out.buttons==0 && out.axes==0);
    nxk_key(&s,26,true); out=nxk_evaluate(&s); assert(axis(out.axes,0)==0 && axis(out.axes,1)==32767);
    nxk_key(&s,7,true); out=nxk_evaluate(&s); assert(axis(out.axes,0)==23170 && axis(out.axes,1)==23170);
    nxk_key(&s,22,true); out=nxk_evaluate(&s); assert(axis(out.axes,1)==0 && axis(out.axes,0)==32767);
    nxk_key(&s,4,true); assert(nxk_evaluate(&s).axes==0);
    nxk_defaults(&s);
    nxk_key(&s,80,true); out=nxk_evaluate(&s); assert(axis(out.axes,2)==-16383);
    nxk_key(&s,21,true); out=nxk_evaluate(&s); assert(axis(out.axes,2)==-9829 && out.buttons==(1ull<<9));
    nxk_defaults(&s);
    nxk_key(&s,26,true); nxk_key(&s,226,true); out=nxk_evaluate(&s); assert(axis(out.axes,1)==12000);
    nxk_defaults(&s);
    for (unsigned a=NXK_A;a<=NXK_DDOWN;a++) {
        nxk_defaults(&s); unsigned key=s.bindings[a].keys[0];
        assert(key && nxk_key(&s,key,true));
        assert(nxk_evaluate(&s).buttons==(1ull<<(a-NXK_A)));
    }
    nxk_defaults(&s); nxk_key(&s,225,true); nxk_key(&s,229,true);
    nxk_key(&s,225,false); assert(nxk_evaluate(&s).buttons==2);
    nxk_key(&s,229,false); assert(nxk_evaluate(&s).buttons==0);
    nxk_defaults(&s); nxk_key(&s,44,true); nxk_release(&s);
    assert(!nxk_key(&s,44,true)); assert(nxk_evaluate(&s).buttons==0);
    nxk_key(&s,44,false); assert(nxk_key(&s,44,true)); assert(nxk_evaluate(&s).buttons==4);
    assert(!nxk_key(&s,NXK_INPUT_COUNT,true)); assert(!nxk_key(&s,0,true));
    nxk_defaults(&s); nxk_key(&s,NXK_MOUSE_LEFT,true); assert(nxk_evaluate(&s).buttons==8);
    nxk_key(&s,9,true); nxk_key(&s,NXK_MOUSE_LEFT,false); assert(nxk_evaluate(&s).buttons==8);
    nxk_key(&s,9,false); nxk_key(&s,NXK_MOUSE_RIGHT,true); assert(nxk_evaluate(&s).buttons==512);
    nxk_key(&s,26,true); nxk_release_mouse(&s);
    assert(nxk_evaluate(&s).buttons==0 && axis(nxk_evaluate(&s).axes,1)==32767);
    nxk_release(&s); assert(nxk_evaluate(&s).buttons==0);
    nxk_defaults(&s); nxk_bind_code(&s,10,NXK_Y);
    assert(s.bindings[NXK_Y].keys[0]==10 && s.bindings[NXK_Y].keys[1]==NXK_MOUSE_LEFT);
    nxk_bind_code(&s,NXK_MOUSE_RIGHT,NXK_Y);
    assert(s.bindings[NXK_Y].keys[0]==10 && s.bindings[NXK_Y].keys[1]==NXK_MOUSE_RIGHT);
    nxk_bind_code(&s,NXK_WHEEL_UP,NXK_DUP); nxk_key(&s,NXK_WHEEL_UP,true);
    assert(nxk_evaluate(&s).buttons==(1ull<<13));
    NXKMouseMotion mouse={0};
    nxk_mouse_move(&mouse,10,5); nxk_mouse_tick(&mouse,0.02,1,100,32,false);
    assert(mouse.x==16000 && mouse.y==8000);
    nxk_mouse_tick(&mouse,0.02,1.02,100,32,false); assert(mouse.x==16000);
    nxk_mouse_tick(&mouse,0.02,1.04,101,32,false); assert(mouse.x==0 && mouse.y==0);
    nxk_mouse_move(&mouse,10,5); nxk_mouse_tick(&mouse,0.02,2,102,32,true); assert(mouse.y==-8000);
    nxk_mouse_tick(&mouse,0.02,2.06,102,32,false); assert(mouse.x==0 && mouse.y==0);
    nxk_mouse_move(&mouse,10000,10000); nxk_mouse_tick(&mouse,0.016,3,103,32,false);
    assert(mouse.x<=23170 && mouse.y<=23170);
    nxk_mouse_clear(&mouse); nxk_mouse_move(&mouse,NAN,1); assert(mouse.dx==0 && mouse.dy==0);
    NXKMouseMotion m30={0},m60={0};
    nxk_mouse_move(&m30,10,0); nxk_mouse_tick(&m30,1.0/30,4,104,32,false);
    nxk_mouse_move(&m60,5,0); nxk_mouse_tick(&m60,1.0/60,4,104,32,false); assert(m30.x==m60.x);
    nxk_defaults(&s); nxk_key(&s,NXK_MOUSE_RIGHT,true); out=nxk_mouse_merge(&s,&m30);
    assert(axis(out.axes,2)==m30.x*3/5 && out.buttons==512);
    _Atomic uint64_t buttons=0,latch=0,axes=0; NXKWriter w={&buttons,&latch,&axes};
    nxk_publish(&w,(NXKOutput){4,123}); nxk_publish(&w,(NXKOutput){0,0});
    assert(atomic_load(&buttons)==0 && atomic_exchange(&latch,0)==4);
    nxk_publish(&w,(NXKOutput){4,123}); assert(atomic_exchange(&latch,0)==4);
    nxk_publish(&w,(NXKOutput){4,123}); assert(atomic_exchange(&latch,0)==0);
    nxk_neutral(&w); assert(atomic_load(&buttons)==0 && atomic_load(&axes)==0 && atomic_load(&latch)==0);
    /* The plugin owns a separate slot: neutralizing it cannot release a physical pad. */
    uint64_t physical=1ull<<7; assert((physical | atomic_load(&buttons))==physical);
    uint8_t uuid[16]; memcpy(uuid,nxk_uuid,16); assert(nxk_uuid_family(uuid));
    uuid[0]++; assert(nxk_uuid_family(uuid)); uuid[15]++; assert(!nxk_uuid_family(uuid));
    NXKPointerPolicy pointer={0};
    assert(nxk_pointer_update(&pointer,true,false,1,1));
    assert(!nxk_pointer_update(&pointer,true,false,1,1.1));
    assert(nxk_pointer_update(&pointer,true,false,2,1.2));
    assert(!nxk_pointer_update(&pointer,true,true,2,1.3));
    assert(nxk_pointer_update(&pointer,true,false,2,2.3));
    assert(nxk_pointer_update(&pointer,false,true,2,2.4));
    assert(!nxk_pointer_update(&pointer,false,false,2,2.5));
    /* A refused release also gets retried; an accepted lock stops retrying. */
    pointer=(NXKPointerPolicy){0};
    assert(nxk_pointer_update(&pointer,true,true,1,10));
    assert(!nxk_pointer_update(&pointer,true,true,1,20));
    assert(nxk_pointer_update(&pointer,false,true,1,20.1));
    assert(!nxk_pointer_update(&pointer,false,true,1,20.2));
    assert(nxk_pointer_update(&pointer,false,true,1,21.2));
    assert(!nxk_pointer_update(&pointer,false,false,1,30));
    /* A root replacement requires notification even while already locked. */
    assert(nxk_pointer_update(&pointer,true,true,2,30.1));
    assert(nxk_pointer_update(&pointer,true,true,3,30.2));
    printf("Input checks passed: full controller, keyboard/mouse coexistence and remapping, mouse velocity/clamping/inversion, sample retention/timeout, release, latch, version family and pointer lock/release retries.\n");
}
