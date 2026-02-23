# Tetris main game loop

import time
import os
import sys
from game import Game

# This version will rely on simple input() for commands.
# The game will only update when a command is entered.

def main():
    game = Game(width=10, height=20)
    
    # Game loop
    while not game.game_over:
        os.system('cls' if os.name == 'nt' else 'clear') # Clear console
        
        # Draw game board
        display_grid = [row[:] for row in game.board.grid]
        if game.current_tetromino:
            for y, row in enumerate(game.current_tetromino.shape):
                for x, cell in enumerate(row):
                    if cell:
                        board_x = game.current_tetromino.x + x
                        board_y = game.current_tetromino.y + y
                        if 0 <= board_x < game.board.width and 0 <= board_y < game.board.height:
                            display_grid[board_y][board_x] = game.current_tetromino.color

        for row in display_grid:
            print(" ".join(["#" if cell else "." for cell in row]))
        
        print(f"Score: {game.score}")
        if game.game_over:
            print("GAME OVER!")
            break # Exit loop if game is over

        # Prompt for input
        print("Enter command (a:left, d:right, s:down, w:rotate, q:quit, [Enter]:fall): ", end='')
        command = input().strip()

        if command == 'q': # Quit game
            break
        elif command == 'a': # Move left
            game.move_tetromino(-1, 0)
        elif command == 'd': # Move right
            game.move_tetromino(1, 0)
        elif command == 's': # Move down (fast drop)
            game.move_tetromino(0, 1)
        elif command == 'w': # Rotate
            game.rotate_tetromino()
        else: # Default: move down
            game.update() 

        # No time.sleep() here, as input() is blocking.
        # Game speed is effectively controlled by how quickly the user enters commands.

    os.system('cls' if os.name == 'nt' else 'clear')
    # Final draw to show GAME OVER (if not already printed)
    display_grid = [row[:] for row in game.board.grid]
    for row in display_grid:
        print(" ".join(["#" if cell else "." for cell in row]))
    print(f"Score: {game.score}")
    print("GAME OVER!")
    time.sleep(3) # Show game over screen for 3 seconds


if __name__ == "__main__":
    main()