#ifndef NXK_POINTER_POLICY_H
#define NXK_POINTER_POLICY_H
#include <stdbool.h>
#include <stdint.h>
typedef struct {
    bool initialized,wanted;
    uint64_t roots;
    double last_attempt;
} NXKPointerPolicy;
/* A new root/key window needs an update even if the requested value is unchanged. */
static inline bool nxk_pointer_update(NXKPointerPolicy *p,bool wanted,bool locked,uint64_t roots,double now) {
    bool changed=!p->initialized || wanted!=p->wanted || roots!=p->roots;
    bool retry=wanted!=locked && now-p->last_attempt>=1.0;
    p->initialized=true; p->wanted=wanted; p->roots=roots;
    if (changed || retry) { p->last_attempt=now; return true; }
    return false;
}
#endif
