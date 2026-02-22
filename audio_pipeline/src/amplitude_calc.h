/**
 * Audio amplitude (RMS) calculator.
 * Computes RMS per 10ms frame, normalized to 0.0-1.0.
 */

#pragma once

#include <cstdint>
#include <cmath>

class AmplitudeCalc {
public:
    /**
     * Compute RMS amplitude of a frame.
     * @param frame  Pointer to int16 samples
     * @param count  Number of samples
     * @return Normalized RMS value in [0.0, 1.0]
     */
    static float compute_rms(const int16_t* frame, int count) {
        if (count <= 0) return 0.0f;

        double sum_sq = 0.0;
        for (int i = 0; i < count; i++) {
            double sample = static_cast<double>(frame[i]);
            sum_sq += sample * sample;
        }

        double rms = std::sqrt(sum_sq / count);
        // Normalize: int16 max is 32768
        float normalized = static_cast<float>(rms / 32768.0);
        return (normalized > 1.0f) ? 1.0f : normalized;
    }
};
