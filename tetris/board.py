# Tetris game board logic

class Board:
    def __init__(self, width, height):
        self.width = width
        self.height = height
        self.grid = [[0 for _ in range(width)] for _ in range(height)]

    def is_valid_position(self, shape, offset_x, offset_y):
        for y, row in enumerate(shape):
            for x, cell in enumerate(row):
                if cell:  # If it's a block
                    board_x = offset_x + x
                    board_y = offset_y + y
                    if not (0 <= board_x < self.width and 0 <= board_y < self.height):
                        return False  # Out of bounds
                    if self.grid[board_y][board_x] != 0:
                        return False  # Collision with existing block
        return True

    def place_tetromino(self, shape, offset_x, offset_y, color):
        for y, row in enumerate(shape):
            for x, cell in enumerate(row):
                if cell:
                    self.grid[offset_y + y][offset_x + x] = color

    def clear_lines(self):
        cleared_lines = 0
        new_grid = []
        for row in self.grid:
            if 0 not in row:  # Full line
                cleared_lines += 1
            else:
                new_grid.append(row)
        
        # Add empty lines to the top
        for _ in range(cleared_lines):
            new_grid.insert(0, [0 for _ in range(self.width)])
        
        self.grid = new_grid
        return cleared_lines

    def display(self):
        for row in self.grid:
            print(" ".join(["#" if cell else "." for cell in row]))