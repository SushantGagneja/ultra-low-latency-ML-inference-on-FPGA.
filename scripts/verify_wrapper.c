#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <stdbool.h>

#include "../esp32_firmware/main/temporal_features.h"
#include "../esp32_firmware/main/quantization.h"

// We include the C files directly to avoid linking issues for this simple test.
#include "../esp32_firmware/main/temporal_features.c"
#include "../esp32_firmware/main/quantization.c"

int main() {
    bnn_feature_state_t fstate;
    bnn_quantizer_t qstate;
    
    bnn_feature_state_init(&fstate);
    bnn_quantizer_init(&qstate);
    
    char line[256];
    // Expected input format: price bid bid_qty ask ask_qty
    while (fgets(line, sizeof(line), stdin)) {
        float price, bid, bid_qty, ask, ask_qty;
        if (sscanf(line, "%f %f %f %f %f", &price, &bid, &bid_qty, &ask, &ask_qty) != 5) {
            continue;
        }
        
        bnn_market_tick_t tick = {
            .price = price,
            .volume = bid_qty + ask_qty,
            .bid = bid,
            .ask = ask
        };
        
        bnn_indicators_t ind;
        bool ready = bnn_feature_update(&fstate, &tick, &ind);
        
        if (ready) {
            uint16_t spike = bnn_quantize_bipolar(&qstate, &ind);
            printf("READY %f %f %f %f %u\n", ind.rsi, ind.momentum, ind.volume_ratio, ind.volatility, spike);
        } else {
            printf("NOT_READY\n");
        }
    }
    return 0;
}
