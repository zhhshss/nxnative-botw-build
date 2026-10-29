#ifndef NXK_INPUT_H
#define NXK_INPUT_H
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

enum NXKAction {
    NXK_LLEFT, NXK_LRIGHT, NXK_LUP, NXK_LDOWN,
    NXK_RLEFT, NXK_RRIGHT, NXK_RUP, NXK_RDOWN,
    NXK_A, NXK_B, NXK_X, NXK_Y, NXK_LS, NXK_RS,
    NXK_L, NXK_R, NXK_ZL, NXK_ZR, NXK_PLUS, NXK_MINUS,
    NXK_DLEFT, NXK_DUP, NXK_DRIGHT, NXK_DDOWN,
    NXK_SLOW, NXK_TOGGLE, NXK_POINTER_TOGGLE, NXK_ACTION_COUNT
};
#define NXK_INPUT_COUNT 272
#define NXK_MOUSE_LEFT 256
#define NXK_MOUSE_RIGHT 257
#define NXK_MOUSE_MIDDLE 258
#define NXK_WHEEL_UP 264
#define NXK_WHEEL_DOWN 265
typedef struct { uint16_t keys[3]; } NXKBinding;
typedef struct {
    bool pressed[NXK_INPUT_COUNT], blocked[NXK_INPUT_COUNT];
    NXKBinding bindings[NXK_ACTION_COUNT];
    int32_t camera_max;
} NXKInput;
typedef struct { uint64_t buttons, axes; } NXKOutput;

static inline void nxk_defaults(NXKInput *s) {
    memset(s, 0, sizeof(*s));
    const NXKBinding bindings[NXK_ACTION_COUNT] = {
        {{4}}, {{7}}, {{26}}, {{22}},             /* A D W S */
        {{80,13}}, {{79,15}}, {{82,12}}, {{81,14}}, /* arrows / J L I K */
        {{8,259}}, {{225,229,260}}, {{44}}, {{9,256}}, /* E / side1, Shift / side2, Space, F / left click */
        {{224,228}}, {{25,258}},                 /* Ctrl, V / middle click: L3 / R3 */
        {{29}}, {{6}}, {{20}}, {{21,257}},        /* Z C Q R / right click: L R ZL ZR */
        {{40}}, {{43}},                          /* Enter Tab */
        {{30}}, {{31}}, {{32}}, {{33}},            /* 1 2 3 4: D-pad */
        {{226,230}}, {{0}}, {{41}}               /* Alt: walk; mapping toggle unbound; Esc: pointer mode */
    };
    memcpy(s->bindings, bindings, sizeof(bindings));
    s->camera_max = 16383;
}
static inline bool nxk_active(const NXKInput *s, enum NXKAction a) {
    for (unsigned i=0; i<3; i++) {
        uint16_t k=s->bindings[a].keys[i];
        if (k && k<NXK_INPUT_COUNT && s->pressed[k] && !s->blocked[k]) return true;
    }
    return false;
}
static inline bool nxk_key(NXKInput *s, unsigned key, bool down) {
    if (!key || key>=NXK_INPUT_COUNT) return false;
    bool fresh=down && !s->pressed[key] && !s->blocked[key];
    s->pressed[key]=down;
    if (!down) s->blocked[key]=false;
    return fresh;
}
/* Require a release after disabling, losing focus, or remapping a held key. */
static inline void nxk_release(NXKInput *s) {
    for (unsigned k=0;k<NXK_INPUT_COUNT;k++) {
        s->blocked[k] |= s->pressed[k];
        s->pressed[k]=false;
    }
}
static inline void nxk_release_mouse(NXKInput *s) {
    for (unsigned k=256;k<NXK_INPUT_COUNT;k++) { s->blocked[k]|=s->pressed[k]; s->pressed[k]=false; }
}
static inline void nxk_bind_code(NXKInput *s,unsigned code,unsigned action) {
    if (!code || code>=NXK_INPUT_COUNT || action>=NXK_ACTION_COUNT) return;
    NXKBinding old=s->bindings[action],next={{0}};
    unsigned n=0;
    if (code<256) next.keys[n++]=(uint16_t)code;
    for (unsigned i=0;i<3 && n<(code<256?3u:2u);i++) {
        unsigned key=old.keys[i];
        if (key && (code<256?key>=256:key<256)) next.keys[n++]=(uint16_t)key;
    }
    if (code>=256) next.keys[n++]=(uint16_t)code;
    s->bindings[action]=next;
}
static inline uint64_t nxk_pack(int32_t lx,int32_t ly,int32_t rx,int32_t ry) {
    return (uint64_t)(uint16_t)lx | (uint64_t)(uint16_t)ly<<16 |
           (uint64_t)(uint16_t)rx<<32 | (uint64_t)(uint16_t)ry<<48;
}
static inline NXKOutput nxk_evaluate(const NXKInput *s) {
    NXKOutput out={0,0};
    for (unsigned a=NXK_A;a<=NXK_DDOWN;a++)
        if (nxk_active(s,(enum NXKAction)a)) out.buttons |= 1ull<<(a-NXK_A);
    int32_t x=nxk_active(s,NXK_LRIGHT)-nxk_active(s,NXK_LLEFT);
    int32_t y=nxk_active(s,NXK_LUP)-nxk_active(s,NXK_LDOWN);
    int32_t rx=nxk_active(s,NXK_RRIGHT)-nxk_active(s,NXK_RLEFT);
    int32_t ry=nxk_active(s,NXK_RUP)-nxk_active(s,NXK_RDOWN);
    int32_t left=nxk_active(s,NXK_SLOW)?12000:32767;
    int32_t right=s->camera_max;
    if (nxk_active(s,NXK_ZR)) right=right*3/5;
    int32_t lm=(x && y)?left*23170/32767:left;
    int32_t rm=(rx && ry)?right*23170/32767:right;
    out.axes=nxk_pack(x*lm,y*lm,rx*rm,ry*rm);
    return out;
}
#endif
