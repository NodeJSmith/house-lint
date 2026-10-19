def first(alpha, beta, gamma):
    total = alpha + beta
    total = total * gamma
    other = alpha - beta
    other = other * gamma
    result = total + other
    result = result / alpha
    return result, total, other
