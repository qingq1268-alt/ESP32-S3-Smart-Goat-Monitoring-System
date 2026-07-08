#pragma once

#include <stdbool.h>
#include <stddef.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define GOAT_BEHAVIOR_NUM_CLASSES 5
#define GOAT_BEHAVIOR_LABEL_MAX 32

typedef struct {
    char label[GOAT_BEHAVIOR_LABEL_MAX];
    float scores[GOAT_BEHAVIOR_NUM_CLASSES];
    float confidence;
    int best_index;
} goat_behavior_model_result_t;

esp_err_t goat_behavior_model_init(void);
void goat_behavior_model_deinit(void);

size_t goat_behavior_model_get_window_size(void);
size_t goat_behavior_model_get_window_step(void);
size_t goat_behavior_model_get_num_classes(void);
const char *goat_behavior_model_get_label(size_t index);

bool goat_behavior_model_infer(const float *window_xyz,
                               size_t point_count,
                               goat_behavior_model_result_t *result);

#ifdef __cplusplus
}
#endif
