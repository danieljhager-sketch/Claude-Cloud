"""A simple number guessing game.

The computer picks a random number and you try to guess it.
Run it with: python hello.py
"""

import random


def play_game(low=1, high=100):
    secret = random.randint(low, high)
    attempts = 0

    print(f"I'm thinking of a number between {low} and {high}.")

    while True:
        guess_text = input("Your guess: ")

        # Make sure the player typed a number
        if not guess_text.isdigit():
            print("Please enter a whole number.")
            continue

        guess = int(guess_text)
        attempts += 1

        if guess < secret:
            print("Too low, try again.")
        elif guess > secret:
            print("Too high, try again.")
        else:
            print(f"You got it in {attempts} tries!")
            break


if __name__ == "__main__":
    play_game()
