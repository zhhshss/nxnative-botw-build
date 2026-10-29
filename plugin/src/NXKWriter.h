#ifndef NXK_WRITER_H
#define NXK_WRITER_H
#include <stdatomic.h>
#include "NXKInput.h"
typedef struct {
    _Atomic uint64_t *buttons, *latch, *axes;
} NXKWriter;
/* The guest reads axes/buttons independently, just as it does for native input. */
static inline void nxk_publish(NXKWriter *w,NXKOutput output) {
    atomic_store_explicit(w->axes,output.axes,memory_order_seq_cst);
    uint64_t old=atomic_exchange_explicit(w->buttons,output.buttons,memory_order_seq_cst);
    atomic_fetch_or_explicit(w->latch,output.buttons & ~old,memory_order_seq_cst);
}
static inline void nxk_neutral(NXKWriter *w) {
    atomic_store_explicit(w->buttons,0,memory_order_seq_cst);
    atomic_store_explicit(w->axes,0,memory_order_seq_cst);
    atomic_store_explicit(w->latch,0,memory_order_seq_cst);
}
#endif
