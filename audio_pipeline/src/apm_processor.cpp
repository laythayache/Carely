/**
 * WebRTC APM wrapper implementation.
 * Configures AEC + NS (High) + AGC2 for noisy home environments.
 */

#include "apm_processor.h"

#include <modules/audio_processing/include/audio_processing.h>
#include <cstdio>
#include <cstring>

APMProcessor::APMProcessor() = default;
APMProcessor::~APMProcessor() = default;

bool APMProcessor::initialize() {
    webrtc::AudioProcessing::Config config;

    // Echo Cancellation
    config.echo_canceller.enabled = true;
    config.echo_canceller.mobile_mode = false;

    // Noise Suppression - High level for TV/fan/AC noise
    config.noise_suppression.enabled = true;
    config.noise_suppression.level =
        webrtc::AudioProcessing::Config::NoiseSuppression::kHigh;

    // Automatic Gain Control (AGC2)
    // GC1 disabled in favor of the newer GC2
    config.gain_controller1.enabled = false;
    config.gain_controller2.enabled = true;
    config.gain_controller2.fixed_digital.gain_db = 0.0f;
    config.gain_controller2.adaptive_digital.enabled = true;

    apm_.reset(webrtc::AudioProcessingBuilder().SetConfig(config).Create());
    if (!apm_) {
        fprintf(stderr, "[apm] Failed to create AudioProcessing instance\n");
        return false;
    }

    stream_config_ = std::make_unique<webrtc::StreamConfig>(SAMPLE_RATE, CHANNELS);

    int err = apm_->Initialize(SAMPLE_RATE, SAMPLE_RATE, SAMPLE_RATE,
                                webrtc::AudioProcessing::kMono,
                                webrtc::AudioProcessing::kMono,
                                webrtc::AudioProcessing::kMono);
    if (err != 0) {
        fprintf(stderr, "[apm] Initialization failed with error: %d\n", err);
        return false;
    }

    fprintf(stdout, "[apm] Initialized (AEC + NS_High + AGC2)\n");
    return true;
}

void APMProcessor::process_capture(const int16_t* input, int16_t* output) {
    if (!apm_) {
        // Pass-through if APM not initialized
        std::memcpy(output, input, FRAME_SIZE * sizeof(int16_t));
        return;
    }

    // Convert int16 to float [-1.0, 1.0] for WebRTC APM
    float float_in[FRAME_SIZE];
    float float_out[FRAME_SIZE];
    for (int i = 0; i < FRAME_SIZE; i++) {
        float_in[i] = static_cast<float>(input[i]) / 32768.0f;
    }

    float* channel_in[1] = {float_in};
    float* channel_out[1] = {float_out};

    // Set the stream delay (estimated 30ms from wireless mic + pipeline)
    apm_->set_stream_delay_ms(35);

    // Process the capture frame
    apm_->ProcessStream(channel_in, *stream_config_, *stream_config_, channel_out);

    // Convert back to int16
    for (int i = 0; i < FRAME_SIZE; i++) {
        float sample = float_out[i] * 32768.0f;
        if (sample > 32767.0f) sample = 32767.0f;
        if (sample < -32768.0f) sample = -32768.0f;
        output[i] = static_cast<int16_t>(sample);
    }
}

void APMProcessor::process_render(const int16_t* render_frame) {
    if (!apm_) return;

    float float_render[FRAME_SIZE];
    for (int i = 0; i < FRAME_SIZE; i++) {
        float_render[i] = static_cast<float>(render_frame[i]) / 32768.0f;
    }

    float* channel_render[1] = {float_render};

    apm_->ProcessReverseStream(channel_render, *stream_config_, *stream_config_, channel_render);
}
