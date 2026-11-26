"""Fitting the colour correction matrix. No hardware imports, no numpy
(this has to run on MicroPython too): every matrix here is at most 3x3, so
plain nested lists and a hand-rolled Gauss-Jordan solve are simpler and
smaller than depending on anything else.
"""


def transpose(matrix):
    return [list(row) for row in zip(*matrix)]


def matmul(a, b):
    rows_a, cols_a = len(a), len(a[0])
    cols_b = len(b[0])
    result = [[0.0] * cols_b for _ in range(rows_a)]
    for i in range(rows_a):
        for k in range(cols_a):
            for j in range(cols_b):
                result[i][j] += a[i][k] * b[k][j]
    return result


def solve(a, b):
    """x such that a @ x == b, for square `a` and multi-column `b`.

    Gauss-Jordan with partial pivoting, on an augmented [a | b] matrix.
    None if `a` is singular, or too close to it to trust: with only a
    handful of calibration points feeding this, a near-zero pivot means
    there is not enough independent information yet, not just noise.
    """
    n = len(a)
    augmented = [list(a[i]) + list(b[i]) for i in range(n)]

    for col in range(n):
        pivot_row = max(range(col, n), key=lambda r: abs(augmented[r][col]))
        if abs(augmented[pivot_row][col]) < 1e-9:
            return None
        augmented[col], augmented[pivot_row] = augmented[pivot_row], augmented[col]

        pivot = augmented[col][col]
        augmented[col] = [v / pivot for v in augmented[col]]

        for row in range(n):
            if row == col:
                continue
            factor = augmented[row][col]
            if factor == 0:
                continue
            augmented[row] = [
                v - factor * augmented[col][i]
                for i, v in enumerate(augmented[row])
            ]

    return [row[n:] for row in augmented]


def fit(points):
    """A 3x3 matrix `m` minimising sum(dist(x @ m, y)**2) over `points`, a
    list of (measured, target) (r, g, b) pairs: apply() is `x @ m`, i.e.
    corrected[c] = sum(x[j] * m[j][c] for j in range(3)).

    None if there are fewer than 3 points, or they are not independent
    enough to determine all 9 entries (e.g. two nearly identical
    measurements) - the normal equations below are singular either way.
    """
    if len(points) < 3:
        return None

    x = [list(measured) for measured, _ in points]
    y = [list(target) for _, target in points]

    xt = transpose(x)
    return solve(matmul(xt, x), matmul(xt, y))


def apply(matrix, rgb):
    return tuple(
        sum(rgb[j] * matrix[j][c] for j in range(3)) for c in range(3)
    )
