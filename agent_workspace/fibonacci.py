# Function to generate Fibonacci series
import sys

def fibonacci(n):
    if n <= 0:
        return []
    elif n == 1:
        return [0]
    fib_series = [0, 1]
    while len(fib_series) < n:
        next_value = fib_series[-1] + fib_series[-2]
        fib_series.append(next_value)
    return fib_series

def main():
    try:
        num_terms = int(sys.argv[1])
    except (IndexError, ValueError):
        print('Usage: python fibonacci.py <number_of_terms>')
        sys.exit(1)
    result = fibonacci(num_terms)
    for term in result:
        print(term)

if __name__ == '__main__':
    main()