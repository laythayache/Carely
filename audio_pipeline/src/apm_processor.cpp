/**
 * WebRTC APM wrapper implementation (legacy 0.3 API).
 * Configures AEC + NS (High) + AGC for noisy home environments.
 */

#include "apm_processor.h"

#include <webrtc/modules/audio_processing/include/audio_processing.h>
#include <webrtc/modules/interface/module_common_types.h>
#include <cstdio>
#include <cstring>

APMProcessor::APMProcessor() = default;
APMProcessor::~APMProcessor() = default;

bool APMProcessor::initialize() {
    // Create APM instance using legacy API
    apm_.reset(webrtc::AudioProcessing::Create());
    if (!apm_) {
        fprintf(stderr, "[apm] Failed to create AudioProcessing instance\n");
        return false;
    }

    // Initialize with sample rates and channel layout
    int err = apm_->Initialize(SAMPLE_RATE, SAMPLE_RATE, SAMPLE_RATE,
                               webrtc::AudioProcessing::kMono,
                               webrtc::AudioProcessing::kMono,
                               webrtc::AudioProcessing::kMono);
    if (err != 0) {
        fprintf(stderr, "[apm] Initialization failed with error: %d\n", err);
        return false;
    }

    // Echo Cancellation
    apm_->echo_cancellation()->Enable(true);
    apm_->echo_cancellation()->enable_drift_compensation(false);

    // Noise Suppression - High level for TV/fan/AC noise
    apm_->noise_suppression()->Enable(true);
    apm_->noise_suppression()->set_level(webrtc::NoiseSuppression::kHigh);

    // Automatic Gain Control (adaptive digital mode)
    apm_->gain_control()->Enable(true);
    apm_->gain_control()->set_mode(webrtc::GainControl::kAdaptiveDigital);
    apm_->gain_control()->set_target_level_dbfs(3);

    // High-pass filter to remove DC offset
    apm_->high_pass_filter()->Enable(true);

    // Create stream config for float processing
    stream_config_ = std::make_unique<webrtc::StreamConfig>(SAMPLE_RATE, CHANNELS);

    fprintf(stdout, "[apm] Initialized (AEC + NS_High + AGC)\n");
    return true;
}

void APMProcessor::process_capture(const int16_t* input, int16_t* output) {
    if (!apm_) {
        // Pass-through if APM not initialized
        std::memcpy(output, input, FRAME_SIZE * sizeof(int16_t));
        return;
    }

    // Use AudioFrame for compatibility with legacy API
    webrtc::AudioFrame frame;
    frame.sample_rate_hz_ = SAMPLE_RATE;
    frame.num_channels_ = CHANNELS;
    frame.samples_per_channel_ = FRAME_SIZE;
    std::memcpy(frame.data_, input, FRAME_SIZE * sizeof(int16_t));

    // Set the stream delay (estimated 35ms from wireless mic + pipeline)
    apm_->set_stream_delay_ms(35);

    // Process the capture frame
    int err = apm_->ProcessStream(&frame);
    if (err != 0) {
        // On error, pass through original
        std::memcpy(output, input, FRAME_SIZE * sizeof(int16_t));
        return;
    }

    std::memcpy(output, frame.data_, FRAME_SIZE * sizeof(int16_t));
}

void APMProcessor::process_render(const int16_t* render_frame) {
    if (!apm_) return;

    webrtc::AudioFrame frame;
    frame.sample_rate_hz_ = SAMPLE_RATE;
    frame.num_channels_ = CHANNELS;
    frame.samples_per_channel_ = FRAME_SIZE;
    std::memcpy(frame.data_, render_frame, FRAME_SIZE * sizeof(int16_t));

    apm_->ProcessReverseStream(&frame);
}
