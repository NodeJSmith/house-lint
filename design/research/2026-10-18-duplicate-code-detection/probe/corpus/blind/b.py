def second(alpha, beta, gamma):
    total = gamma + gamma
    other = total * alpha
    total = beta - alpha
    beta = other * total
    gamma = beta + beta
    alpha = gamma / total
    return other, alpha, beta
