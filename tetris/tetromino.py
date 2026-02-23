# Tetromino shapes and rotations

import random

TETROMINOS = {
    'I': {'shape': [[1, 1, 1, 1]], 'color': 1},
    'J': {'shape': [[1, 0, 0], [1, 1, 1]], 'color': 2},
    'L': {'shape': [[0, 0, 1], [1, 1, 1]], 'color': 3},
    'O': {'shape': [[1, 1], [1, 1]], 'color': 4},
    'S': {'shape': [[0, 1, 1], [1, 1, 0]], 'color': 5},
    'T': {'shape': [[0, 1, 0], [1, 1, 1]], 'color': 6},
    'Z': {'shape': [[1, 1, 0], [0, 1, 1]], 'color': 7},
}

class Tetromino:
    def __init__(self):
        self.shape_name = random.choice(list(TETROMINOS.keys()))
        self.data = TETROMINOS[self.shape_name]
        self.shape = self.data['shape']
        self.color = self.data['color']
        self.x = 0
        self.y = 0

    def rotate(self):
        self.shape = [list(row) for row in zip(*self.shape[::-1])] # Rotate 90 degrees clockwise