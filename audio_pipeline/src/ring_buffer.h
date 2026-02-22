/**
 * Lock-free Single-Producer Single-Consumer (SPSC) ring buffer.
 * Used for passing audio frames between threads without locks.
 *
 * Template parameters:
 *   T - element type (typically int16_t for audio samples)
 *   N - buffer capacity (MUST be a power of 2)
 */

#pragma once

#include <array>
#include <atomic>
#include <cstddef>
#include <cstring>

template <typename T, size_t N>
class RingBuffer {
    static_assert((N & (N - 1)) == 0, "N must be a power of 2");

public:
    RingBuffer() : head_(0), tail_(0) {}

    /**
     * Push 'count' elements into the buffer.
     * Returns true if all elements were pushed, false if buffer is full.
     * ONLY call from the producer thread.
     */
    bool push(const T* data, size_t count) {
        size_t head = head_.load(std::memory_order_relaxed);
        size_t tail = tail_.load(std::memory_order_acquire);
        size_t available = N - (head - tail);

        if (count > available) {
            return false;  // Buffer full
        }

        size_t idx = head & (N - 1);
        size_t first_chunk = std::min(count, N - idx);
        std::memcpy(&buf_[idx], data, first_chunk * sizeof(T));

        if (count > first_chunk) {
            std::memcpy(&buf_[0], data + first_chunk, (count - first_chunk) * sizeof(T));
        }

        head_.store(head + count, std::memory_order_release);
        return true;
    }

    /**
     * Pop up to 'max_count' elements from the buffer.
     * Returns the number of elements actually popped.
     * ONLY call from the consumer thread.
     */
    size_t pop(T* data, size_t max_count) {
        size_t tail = tail_.load(std::memory_order_relaxed);
        size_t head = head_.load(std::memory_order_acquire);
        size_t available = head - tail;

        size_t count = std::min(max_count, available);
        if (count == 0) return 0;

        size_t idx = tail & (N - 1);
        size_t first_chunk = std::min(count, N - idx);
        std::memcpy(data, &buf_[idx], first_chunk * sizeof(T));

        if (count > first_chunk) {
            std::memcpy(data + first_chunk, &buf_[0], (count - first_chunk) * sizeof(T));
        }

        tail_.store(tail + count, std::memory_order_release);
        return count;
    }

    /**
     * Returns the number of elements available for reading.
     */
    size_t available() const {
        size_t head = head_.load(std::memory_order_acquire);
        size_t tail = tail_.load(std::memory_order_acquire);
        return head - tail;
    }

    /**
     * Returns the number of free slots for writing.
     */
    size_t free_space() const {
        return N - available();
    }

    /**
     * Reset the buffer (NOT thread-safe, only call when both threads are stopped).
     */
    void reset() {
        head_.store(0, std::memory_order_relaxed);
        tail_.store(0, std::memory_order_relaxed);
    }

private:
    std::array<T, N> buf_;
    alignas(64) std::atomic<size_t> head_;  // Cache-line aligned
    alignas(64) std::atomic<size_t> tail_;  // Separate cache line to avoid false sharing
};
