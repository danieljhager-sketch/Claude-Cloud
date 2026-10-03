"""A simple number guessing game.

The computer picks a random number and you try to guess it
in 7 guesses or fewer.
Run it with: python hello.py
"""

import random


def play_game(low=1, high=100, max_guesses=7):
    secret = random.randint(low, high)
    attempts = 0

    print(f"I'm thinking of a number between {low} and {high}.")
    print(f"You have {max_guesses} guesses.")

    while attempts < max_guesses:
        guess_text = input("Your guess: ")

        # Make sure the player typed a number
        if not guess_text.isdigit():
            print("Please enter a whole number.")
            continue

        guess = int(guess_text)
        attempts += 1
        remaining = max_guesses - attempts

        if guess == secret:
            print(f"You got it in {attempts} tries!")
            return

        hint = "Too low" if guess < secret else "Too high"
        if remaining > 0:
            word = "guess" if remaining == 1 else "guesses"
            print(f"{hint}, try again. ({remaining} {word} left)")
        else:
            print(f"{hint}.")

    print(f"You're out of guesses! The number was {secret}.")


if __name__ == "__main__":
    play_game()
