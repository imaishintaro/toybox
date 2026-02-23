# Tetris game state and logic

from board import Board
from tetromino import Tetromino

class Game:
    def __init__(self, width, height):
        self.board = Board(width, height)
        self.current_tetromino = None
        self.score = 0
        self.game_over = False
        self._new_tetromino()

    def _new_tetromino(self):
        self.current_tetromino = Tetromino()
        self.current_tetromino.x = self.board.width // 2 - len(self.current_tetromino.shape[0]) // 2
        self.current_tetromino.y = 0
        
        if not self.board.is_valid_position(self.current_tetromino.shape, self.current_tetromino.x, self.current_tetromino.y):
            self.game_over = True

    def move_tetromino(self, dx, dy):
        if self.game_over:
            return
        
        new_x = self.current_tetromino.x + dx
        new_y = self.current_tetromino.y + dy
        
        if self.board.is_valid_position(self.current_tetromino.shape, new_x, new_y):
            self.current_tetromino.x = new_x
            self.current_tetromino.y = new_y
            return True
        elif dy == 1:  # Collision when moving down, so place the tetromino
            self.board.place_tetromino(self.current_tetromino.shape, self.current_tetromino.x, self.current_tetromino.y, self.current_tetromino.color)
            self.score += self.board.clear_lines() * 100  # Example scoring
            self._new_tetromino()
            return False
        return False

    def rotate_tetromino(self):
        if self.game_over:
            return
        
        original_shape = self.current_tetromino.shape
        self.current_tetromino.rotate()
        
        if not self.board.is_valid_position(self.current_tetromino.shape, self.current_tetromino.x, self.current_tetromino.y):
            self.current_tetromino.shape = original_shape  # Revert rotation if invalid

    def update(self):
        if self.game_over:
            return
        
        self.move_tetromino(0, 1) # Move down automatically

    def draw(self):
        display_grid = [row[:] for row in self.board.grid] # Copy board grid
        
        if not self.game_over and self.current_tetromino:
            for y, row in enumerate(self.current_tetromino.shape):
                for x, cell in enumerate(row):
                    if cell:
                        board_x = self.current_tetromino.x + x
                        board_y = self.current_tetromino.y + y
                        if 0 <= board_x < self.board.width and 0 <= board_y < self.board.height:
                            display_grid[board_y][board_x] = self.current_tetromino.color
        
        for row in display_grid:
            print(" ".join(["#" if cell else "." for cell in row]))
        print(f"Score: {self.score}")
        if self.game_over:
            print("GAME OVER!")