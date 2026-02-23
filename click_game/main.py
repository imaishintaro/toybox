# Click Game with Tkinter

import tkinter as tk
import random
import time

class ClickGame:
    def __init__(self, master):
        self.master = master
        master.title("Click Game")

        self.score = 0
        self.time_left = 30 # seconds
        self.game_running = False

        self.score_label = tk.Label(master, text=f"Score: {self.score}", font=("Arial", 16))
        self.score_label.pack(pady=10)

        self.time_label = tk.Label(master, text=f"Time: {self.time_left}", font=("Arial", 16))
        self.time_label.pack(pady=5)

        self.click_button = tk.Button(master, text="Click Me!", command=self.click_button_action, font=("Arial", 14), width=10, height=2)
        self.click_button.place(x=200, y=150) # Initial position

        self.start_button = tk.Button(master, text="Start Game", command=self.start_game, font=("Arial", 14))
        self.start_button.pack(pady=10)

        self.master.geometry("500x400") # Set window size

    def start_game(self):
        if not self.game_running:
            self.score = 0
            self.time_left = 30
            self.game_running = True
            self.update_score()
            self.update_time()
            self.move_button()
            self.start_button.config(state=tk.DISABLED)

    def click_button_action(self):
        if self.game_running:
            self.score += 1
            self.update_score()
            self.move_button()

    def move_button(self):
        # Get window dimensions
        window_width = self.master.winfo_width()
        window_height = self.master.winfo_height()

        # Get button dimensions (approximate, as it might not be rendered yet)
        button_width = self.click_button.winfo_width() if self.click_button.winfo_width() > 1 else 80 # Default if not yet rendered
        button_height = self.click_button.winfo_height() if self.click_button.winfo_height() > 1 else 40 # Default if not yet rendered

        # Calculate random position within window bounds
        max_x = max(1, window_width - button_width - 20) # -20 for some padding
        max_y = max(1, window_height - button_height - 20) # -20 for some padding

        new_x = random.randint(0, max_x)
        new_y = random.randint(50, max_y) # Start y a bit lower to avoid labels
        
        self.click_button.place(x=new_x, y=new_y)

    def update_score(self):
        self.score_label.config(text=f"Score: {self.score}")

    def update_time(self):
        if self.game_running:
            if self.time_left > 0:
                self.time_left -= 1
                self.time_label.config(text=f"Time: {self.time_left}")
                self.master.after(1000, self.update_time) # Call again after 1 second
            else:
                self.game_running = False
                self.start_button.config(state=tk.NORMAL)
                self.click_button.place_forget() # Hide button
                tk.messagebox.showinfo("Game Over", f"Time's up! Your final score is: {self.score}")

if __name__ == "__main__":
    root = tk.Tk()
    game = ClickGame(root)
    root.mainloop()
