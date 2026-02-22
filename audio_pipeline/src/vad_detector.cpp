/**
 * Energy-based VAD detector implementation.
 *
 * Works on APM-cleaned audio (post noise suppression) so a simple
 * energy threshold is effective. Tracks a slow-adapting noise floor
 * and triggers on energy exceeding threshold above floor.
 *
 * Aggressiveness mapping:
 *   0 = low threshold  (sensitive, more false positives)
 *   1 = medium-low
 *   2 = medium-high
 *   3 = high threshold (aggressive, fewer false positives)
 */

#include "vad_detector.h"
#include <cstdio>
#include <cstring>
#include <cmath>
#include <algorithm>

// Energy threshold multipliers for each aggressiveness level
static constexpr float THRESHOLD_MULTIPLIERS[4] = {
    3.0f,   // 0: very sensitive
    5.0f,   // 1: sensitive
    8.0f,   // 2: moderate
    12.0f,  // 3: aggressive (recommended for noisy homes)
};

VadDetector::VadDetector(const Config& config) : config_(config) {
    speech_start_frames_ = config.speech_start_ms / 10;
    silence_end_frames_ = config.silence_end_ms / 10;
    min_speech_frames_ = config.min_speech_ms / 10;
    max_utterance_frames_ = config.max_utterance_ms / 10;

    preroll_capacity_ = (config.preroll_ms / 10) * FRAME_SIZE;
    preroll_buffer_.resize(preroll_capacity_, 0);

    // Set energy threshold based on aggressiveness
    int agg = std::clamp(config.aggressiveness, 0, 3);
    energy_threshold_ = 0.005f * THRESHOLD_MULTIPLIERS[agg];
}

bool VadDetector::initialize() {
    fprintf(stdout, "[vad] Initialized energy-based VAD "
            "(aggressiveness=%d, threshold=%.4f, silence_end=%dms)\n",
            config_.aggressiveness, energy_threshold_, config_.silence_end_ms);
    return true;
}

float VadDetector::compute_rms(const int16_t* frame) const {
    double sum_sq = 0.0;
    for (int i = 0; i < FRAME_SIZE; i++) {
        double s = static_cast<double>(frame[i]) / 32768.0;
        sum_sq += s * s;
    }
    return static_cast<float>(std::sqrt(sum_sq / FRAME_SIZE));
}

bool VadDetector::is_voiced(const int16_t* frame) const {
    float rms = compute_rms(frame);
    // Voiced if energy exceeds threshold above tracked noise floor
    return rms > (noise_floor_ + energy_threshold_);
}

VadDetector::Event VadDetector::process_frame(const int16_t* frame) {
    float rms = compute_rms(frame);
    bool voiced = rms > (noise_floor_ + energy_threshold_);

    // Update noise floor during silence (slow adaptation)
    if (!voiced && state_ == InternalState::WAITING) {
        noise_floor_ = noise_floor_alpha_ * noise_floor_ + (1.0f - noise_floor_alpha_) * rms;
    }

    switch (state_) {
        case InternalState::WAITING: {
            std::memcpy(&preroll_buffer_[preroll_write_pos_], frame, FRAME_SIZE * sizeof(int16_t));
            preroll_write_pos_ = (preroll_write_pos_ + FRAME_SIZE) % preroll_capacity_;

            if (voiced) {
                voiced_frame_count_++;
                if (voiced_frame_count_ >= speech_start_frames_) {
                    state_ = InternalState::SPEAKING;
                    total_speech_frames_ = 0;
                    silence_frame_count_ = 0;

                    speech_buffer_.clear();
                    speech_buffer_.reserve(preroll_capacity_ + SAMPLE_RATE * 30);

                    // Copy pre-roll
                    size_t read_pos = preroll_write_pos_;
                    for (size_t i = 0; i < preroll_capacity_; i += FRAME_SIZE) {
                        speech_buffer_.insert(speech_buffer_.end(),
                                            &preroll_buffer_[read_pos],
                                            &preroll_buffer_[read_pos] + FRAME_SIZE);
                        read_pos = (read_pos + FRAME_SIZE) % preroll_capacity_;
                    }

                    speech_buffer_.insert(speech_buffer_.end(), frame, frame + FRAME_SIZE);
                    total_speech_frames_++;

                    return Event::VAD_START;
                }
            } else {
                voiced_frame_count_ = 0;
            }
            break;
        }

        case InternalState::SPEAKING: {
            speech_buffer_.insert(speech_buffer_.end(), frame, frame + FRAME_SIZE);
            total_speech_frames_++;

            if (voiced) {
                silence_frame_count_ = 0;
            } else {
                silence_frame_count_++;
            }

            bool silence_end = silence_frame_count_ >= silence_end_frames_;
            bool max_duration = total_speech_frames_ >= max_utterance_frames_;

            if (silence_end || max_duration) {
                if (max_duration) {
                    fprintf(stdout, "[vad] Max utterance reached (%ds)\n",
                            config_.max_utterance_ms / 1000);
                }

                if (total_speech_frames_ >= min_speech_frames_) {
                    state_ = InternalState::DONE;
                    return Event::VAD_END;
                } else {
                    fprintf(stdout, "[vad] Speech too short (%dms), discarding\n",
                            total_speech_frames_ * 10);
                    reset();
                }
            }
            break;
        }

        case InternalState::DONE:
            break;
    }

    return Event::NONE;
}

void VadDetector::reset() {
    state_ = InternalState::WAITING;
    voiced_frame_count_ = 0;
    silence_frame_count_ = 0;
    total_speech_frames_ = 0;
    speech_buffer_.clear();
}

bool VadDetector::force_end() {
    if (state_ == InternalState::SPEAKING && total_speech_frames_ >= min_speech_frames_) {
        state_ = InternalState::DONE;
        return true;
    }
    return false;
}
