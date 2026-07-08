#include "goat_behavior_model.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

#include "dl_model_base.hpp"
#include "dl_module_softmax.hpp"
#include "dl_tensor_base.hpp"
#include "esp_log.h"
#include "model/model_norm.h"

static const char *TAG = "GOAT_MODEL";

static_assert(MODEL_NUM_CLASSES == GOAT_BEHAVIOR_NUM_CLASSES, "Model class count must stay at 5.");
static_assert(MODEL_INPUT_CHANNELS == 4, "Model input channels must be 4 (X, Y, Z, |a|).");

extern const uint8_t goat_behavior_model_espdl_start[] asm("_binary_goat_cnn_5cls_s3_int8_espdl_start");

namespace {

typedef struct {
    dl::Model *model;
    dl::TensorBase *input_stage;
    dl::TensorBase *output_probs;
    dl::module::Softmax *softmax;
    std::vector<int> input_shape;
    std::vector<int> output_shape;
    size_t window_size;
    size_t window_step;
    int channel_axis;
    int time_axis;
    bool output_is_probability;
    bool initialized;
} goat_model_state_t;

goat_model_state_t g_state = {};

size_t shape_product(const std::vector<int> &shape)
{
    size_t total = 1;
    for (int dim : shape) {
        if (dim <= 0) {
            return 0;
        }
        total *= static_cast<size_t>(dim);
    }
    return total;
}

std::vector<int> make_strides(const std::vector<int> &shape)
{
    std::vector<int> strides(shape.size(), 1);
    for (int i = static_cast<int>(shape.size()) - 2; i >= 0; --i) {
        strides[i] = strides[i + 1] * shape[i + 1];
    }
    return strides;
}

void destroy_state(void)
{
    delete g_state.softmax;
    delete g_state.output_probs;
    delete g_state.input_stage;
    delete g_state.model;

    g_state.softmax = nullptr;
    g_state.output_probs = nullptr;
    g_state.input_stage = nullptr;
    g_state.model = nullptr;
    g_state.input_shape.clear();
    g_state.output_shape.clear();
    g_state.window_size = 0;
    g_state.window_step = 0;
    g_state.channel_axis = -1;
    g_state.time_axis = -1;
    g_state.output_is_probability = false;
    g_state.initialized = false;
}

bool resolve_input_layout(const std::vector<int> &shape, int *channel_axis, int *time_axis, size_t *window_size)
{
    int resolved_channel_axis = -1;
    int resolved_time_axis = -1;

    for (size_t i = 0; i < shape.size(); ++i) {
        if (shape[i] == MODEL_INPUT_CHANNELS && resolved_channel_axis < 0) {
            resolved_channel_axis = static_cast<int>(i);
            continue;
        }

        if (shape[i] > 1 && resolved_time_axis < 0) {
            resolved_time_axis = static_cast<int>(i);
            continue;
        }

        if (shape[i] != 1) {
            return false;
        }
    }

    if (resolved_channel_axis < 0 || resolved_time_axis < 0) {
        return false;
    }

    size_t resolved_window_size = static_cast<size_t>(shape[resolved_time_axis]);
    if (shape_product(shape) != static_cast<size_t>(MODEL_INPUT_CHANNELS) * resolved_window_size) {
        return false;
    }

    *channel_axis = resolved_channel_axis;
    *time_axis = resolved_time_axis;
    *window_size = resolved_window_size;
    return true;
}

bool model_output_is_softmax(dl::Model *model)
{
    fbs::FbsModel *fbs_model = model->get_fbs_model();
    fbs_model->load_map();

    std::vector<std::string> graph_outputs = fbs_model->get_graph_outputs();
    if (graph_outputs.empty()) {
        fbs_model->clear_map();
        return false;
    }

    const std::string output_name = graph_outputs.front();
    std::vector<std::string> nodes = fbs_model->topological_sort();

    for (auto it = nodes.rbegin(); it != nodes.rend(); ++it) {
        std::vector<std::string> inputs;
        std::vector<std::string> outputs;
        if (fbs_model->get_operation_inputs_and_outputs(*it, inputs, outputs) != ESP_OK) {
            continue;
        }
        if (std::find(outputs.begin(), outputs.end(), output_name) != outputs.end()) {
            const bool is_softmax = (fbs_model->get_operation_type(*it) == "Softmax");
            fbs_model->clear_map();
            return is_softmax;
        }
    }

    fbs_model->clear_map();
    return false;
}

dl::quant_type_t get_softmax_quant_type(dl::dtype_t dtype)
{
    if (dtype == dl::DATA_TYPE_INT8 || dtype == dl::DATA_TYPE_UINT8) {
        return dl::QUANT_TYPE_SYMM_8BIT;
    }
    if (dtype == dl::DATA_TYPE_INT16 || dtype == dl::DATA_TYPE_UINT16) {
        return dl::QUANT_TYPE_SYMM_16BIT;
    }
    return dl::QUANT_TYPE_FLOAT32;
}

bool pack_input_tensor(float *dst, const float *window_xyz, size_t point_count)
{
    if (!dst || !window_xyz || point_count != g_state.window_size ||
        g_state.channel_axis < 0 || g_state.time_axis < 0) {
        return false;
    }

    const size_t W = g_state.window_size;
    const int C = MODEL_INPUT_CHANNELS; // 4: X, Y, Z, |a|
    const size_t total = shape_product(g_state.input_shape);
    const std::vector<int> strides = make_strides(g_state.input_shape);

    std::vector<float> tmp(W * C, 0.0f);

    // Step 1: Copy XYZ and compute |a| magnitude channel
    float ch_mean[4] = {0.0f, 0.0f, 0.0f, 0.0f};

    for (size_t t = 0; t < W; ++t) {
        float x = window_xyz[t * 3 + 0];
        float y = window_xyz[t * 3 + 1];
        float z = window_xyz[t * 3 + 2];
        float mag = sqrtf(x * x + y * y + z * z);

        tmp[t * C + 0] = x;
        tmp[t * C + 1] = y;
        tmp[t * C + 2] = z;
        tmp[t * C + 3] = mag;

        ch_mean[0] += x;
        ch_mean[1] += y;
        ch_mean[2] += z;
        ch_mean[3] += mag;
    }

    // Step 2: Per-window centering (subtract per-channel mean of this window)
    float inv_W = 1.0f / (float)W;
    for (int c = 0; c < C; ++c) {
        ch_mean[c] *= inv_W;
    }

    // Step 3: Center and normalize by global std, then place values into the
    // resolved model layout. This supports 4D Conv2d exports such as
    // [1, 4, 120, 1] or [1, 120, 1, 4].
    memset(dst, 0, sizeof(float) * total);
    for (size_t t = 0; t < W; ++t) {
        for (int c = 0; c < C; ++c) {
            float val = tmp[t * C + c] - ch_mean[c];
            float std_val = NORM_STD[c] == 0.0f ? 1.0f : NORM_STD[c];
            val /= std_val;
            const size_t flat_index =
                static_cast<size_t>(t) * static_cast<size_t>(strides[g_state.time_axis]) +
                static_cast<size_t>(c) * static_cast<size_t>(strides[g_state.channel_axis]);
            dst[flat_index] = val;
        }
    }

    return true;
}

void fill_result_from_output(goat_behavior_model_result_t *result)
{
    memset(result, 0, sizeof(*result));

    dl::TensorBase *model_output = g_state.model->get_output();
    if (g_state.output_is_probability) {
        g_state.output_probs->assign(model_output);
    } else {
        g_state.softmax->run(model_output, g_state.output_probs, dl::RUNTIME_MODE_SINGLE_CORE);
    }

    float *scores = g_state.output_probs->get_element_ptr<float>();
    int best_index = 0;
    float best_score = scores[0];

    for (int i = 0; i < MODEL_NUM_CLASSES; ++i) {
        result->scores[i] = scores[i];
        if (scores[i] > best_score) {
            best_score = scores[i];
            best_index = i;
        }
    }

    result->best_index = best_index;
    result->confidence = best_score;
    snprintf(result->label, sizeof(result->label), "%s", ACT_LABELS[best_index]);
}

} // namespace

extern "C" esp_err_t goat_behavior_model_init(void)
{
    if (g_state.initialized) {
        return ESP_OK;
    }

    destroy_state();

    /* Set max_internal_size to 32KB to force most allocations to PSRAM.
     * This prevents internal SRAM exhaustion during model loading. */
    const int max_internal_size = 32 * 1024;

    ESP_LOGI(TAG, "Loading model from Flash RODATA with max_internal_size=%d bytes...", max_internal_size);

    g_state.model = new dl::Model(reinterpret_cast<const char *>(goat_behavior_model_espdl_start),
                                  fbs::MODEL_LOCATION_IN_FLASH_RODATA,
                                  max_internal_size,
                                  dl::MEMORY_MANAGER_GREEDY,
                                  nullptr,
                                  true);
    if (!g_state.model) {
        ESP_LOGE(TAG, "Failed to create ESP-DL model instance.");
        destroy_state();
        return ESP_FAIL;
    }

    if (g_state.model->get_inputs().size() != 1 || g_state.model->get_outputs().size() != 1) {
        ESP_LOGE(TAG, "Expected exactly one model input and one model output.");
        destroy_state();
        return ESP_FAIL;
    }

    dl::TensorBase *model_input = g_state.model->get_input();
    dl::TensorBase *model_output = g_state.model->get_output();
    g_state.input_shape = model_input->get_shape();
    g_state.output_shape = model_output->get_shape();

    if (!resolve_input_layout(
            g_state.input_shape, &g_state.channel_axis, &g_state.time_axis, &g_state.window_size)) {
        ESP_LOGE(TAG,
                 "Unsupported model input shape %s. Expected one 4-channel dim and one time dim.",
                 dl::vector_to_string(g_state.input_shape).c_str());
        destroy_state();
        return ESP_FAIL;
    }

    if (g_state.window_size == 0) {
        ESP_LOGE(TAG, "Resolved model window size is zero.");
        destroy_state();
        return ESP_FAIL;
    }

    if (model_output->get_size() != MODEL_NUM_CLASSES) {
        ESP_LOGE(TAG,
                 "Model output size mismatch. Expected %d classes but got %d.",
                 MODEL_NUM_CLASSES,
                 model_output->get_size());
        destroy_state();
        return ESP_FAIL;
    }

    g_state.window_step = std::max<size_t>(1, g_state.window_size / 2);
    g_state.output_is_probability = model_output_is_softmax(g_state.model);

    g_state.input_stage = new dl::TensorBase(g_state.input_shape, nullptr, 0, dl::DATA_TYPE_FLOAT);
    g_state.output_probs = new dl::TensorBase(g_state.output_shape, nullptr, 0, dl::DATA_TYPE_FLOAT);
    if (!g_state.input_stage || !g_state.output_probs) {
        ESP_LOGE(TAG, "Failed to allocate model staging tensors.");
        destroy_state();
        return ESP_FAIL;
    }

    if (!g_state.output_is_probability) {
        g_state.softmax = new dl::module::Softmax(
            "goat_behavior_softmax", -1, dl::MODULE_NON_INPLACE, get_softmax_quant_type(model_output->get_dtype()));
        if (!g_state.softmax) {
            ESP_LOGE(TAG, "Failed to create softmax postprocessor.");
            destroy_state();
            return ESP_FAIL;
        }
    }

    g_state.initialized = true;

    ESP_LOGI(TAG,
             "CNN model ready. Input shape=%s, dtype=%s, exp=%d, output shape=%s, output dtype=%s, exp=%d.",
             dl::vector_to_string(g_state.input_shape).c_str(),
             model_input->get_dtype_string(),
             model_input->get_exponent(),
             dl::vector_to_string(g_state.output_shape).c_str(),
             model_output->get_dtype_string(),
             model_output->get_exponent());
    ESP_LOGI(TAG,
             "Resolved layout: channel_axis=%d, time_axis=%d, window_size=%u, step=%u, softmax_in_graph=%s.",
             g_state.channel_axis,
             g_state.time_axis,
             static_cast<unsigned>(g_state.window_size),
             static_cast<unsigned>(g_state.window_step),
             g_state.output_is_probability ? "true" : "false");
    for (int i = 0; i < MODEL_NUM_CLASSES; ++i) {
        ESP_LOGI(TAG, "Label[%d] = %s", i, ACT_LABELS[i]);
    }

    return ESP_OK;
}

extern "C" void goat_behavior_model_deinit(void)
{
    destroy_state();
}

extern "C" size_t goat_behavior_model_get_window_size(void)
{
    return g_state.window_size;
}

extern "C" size_t goat_behavior_model_get_window_step(void)
{
    return g_state.window_step;
}

extern "C" size_t goat_behavior_model_get_num_classes(void)
{
    return GOAT_BEHAVIOR_NUM_CLASSES;
}

extern "C" const char *goat_behavior_model_get_label(size_t index)
{
    if (index >= GOAT_BEHAVIOR_NUM_CLASSES) {
        return "Unknown";
    }
    return ACT_LABELS[index];
}

extern "C" bool goat_behavior_model_infer(const float *window_xyz,
                                           size_t point_count,
                                           goat_behavior_model_result_t *result)
{
    if (!g_state.initialized || !window_xyz || !result) {
        return false;
    }

    if (point_count != g_state.window_size) {
        ESP_LOGE(TAG,
                 "Input point count mismatch. Expected %u points but got %u.",
                 static_cast<unsigned>(g_state.window_size),
                 static_cast<unsigned>(point_count));
        return false;
    }

    float *staged_input = g_state.input_stage->get_element_ptr<float>();
    if (!pack_input_tensor(staged_input, window_xyz, point_count)) {
        ESP_LOGE(TAG, "Failed to pack normalized input tensor.");
        return false;
    }

    g_state.model->run(g_state.input_stage, dl::RUNTIME_MODE_SINGLE_CORE);

    fill_result_from_output(result);

    return true;
}
