#pragma once

#define MODEL_NUM_CLASSES 5
#define MODEL_INPUT_CHANNELS 4
#define MODEL_WINDOW_SIZE 120

/* per-window centering + global std normalization (4 channels: X, Y, Z, |a|) */
static const float NORM_STD[4] = {0.110051081f, 0.159435093f, 0.168068826f, 0.099703118f};

static const char ACT_LABELS[MODEL_NUM_CLASSES][24] = {
    "Displacement",
    "Grazing",
    "Ruminating_Chewing",
    "Other",
    "Resting"
};
