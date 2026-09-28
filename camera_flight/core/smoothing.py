"""Curve filters used to clean up recorded takes."""

import math


def gaussian_kernel(sigma):
    radius = max(1, int(math.ceil(sigma * 3.0)))
    two_s2 = 2.0 * sigma * sigma
    weights = [math.exp(-(i * i) / two_s2) for i in range(-radius, radius + 1)]
    return radius, weights


def gaussian_smooth(values, sigma, pin_ends=True):
    """Gaussian blur a sequence of samples.

    With ``pin_ends`` the first and last values are kept exactly and the
    filter fades in over the kernel radius, so a take still starts and ends
    where it was recorded.
    """
    n = len(values)
    if n < 3 or sigma <= 0.0:
        return list(values)
    radius, kernel = gaussian_kernel(sigma)
    out = []
    for i in range(n):
        acc = 0.0
        wsum = 0.0
        for k, w in enumerate(kernel):
            j = i + k - radius
            if 0 <= j < n:
                acc += values[j] * w
                wsum += w
        out.append(acc / wsum)
    if pin_ends:
        span = min(radius, n // 2)
        for i in range(span + 1):
            t = i / span if span else 1.0
            t = t * t * (3.0 - 2.0 * t)
            out[i] = values[i] + (out[i] - values[i]) * t
            j = n - 1 - i
            out[j] = values[j] + (out[j] - values[j]) * t
    return out


def rdp_indices(xs, ys, epsilon):
    """Ramer-Douglas-Peucker on a function curve.

    Uses vertical (value) error, which is what matters for F-Curves.
    Returns the sorted indices of samples to keep.
    """
    n = len(xs)
    if n <= 2 or epsilon <= 0.0:
        return list(range(n))
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        xa, ya, xb, yb = xs[a], ys[a], xs[b], ys[b]
        span = xb - xa
        dmax = -1.0
        idx = -1
        for i in range(a + 1, b):
            t = (xs[i] - xa) / span if span else 0.0
            d = abs(ys[i] - (ya + (yb - ya) * t))
            if d > dmax:
                dmax = d
                idx = i
        if dmax > epsilon and idx > 0:
            keep[idx] = True
            stack.append((a, idx))
            stack.append((idx, b))
    return [i for i in range(n) if keep[i]]
