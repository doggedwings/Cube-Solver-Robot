# Rubik's Cube Solver Robot

An autonomous Rubik's Cube solving robot built around an ESP32, six NEMA 17 stepper motors, a custom 3D printed frame, and a web app that scans the cube using a phone camera and solves it with the Kociemba algorithm, which can solve a scrambled 3x3 cube in under 10 seconds. 

<img width="2160" height="2880" alt="image" src="https://github.com/user-attachments/assets/00a8a205-b462-490b-b0b2-0f6d31ea2847" />

## WATCH THE SOLVE HERE 
https://youtube.com/shorts/RGmkUIyBiMk?feature=share

## Why I Built This
I've been solving Rubik's Cubes casually for almost a decade, and my best time is still over 20 seconds. I figured if I couldn't get faster, I'd build something that could. This robot scans a scrambled cube with a phone camera, computes the optimal solution, and physically solves it in well under 10 seconds using 6 stepper motors.

## How It Works

You scramble a cube and place it in the frame. Open the web app on your phone, calibrate the 6 colors, and scan all 6 faces by holding the cube up to the camera one face at a time. The app figures out the cube state, runs it through a solving algorithm, and sends the move sequence to the robot, which then physically executes every turn until the cube is solved.

## Hardware Specs

The Brain: ESP32-WROOM-32D, drives all 6 stepper motors over Serial commands
The Motors: 6x NEMA 17, one per cube face
The Drivers: 6x A4988 stepper drivers, full-step mode, ~50 steps per 90° turn
Power: 12V wall adapter for motors, USB for ESP32 logic
The Eyes: Phone camera or webcam for scanning cube faces
The Solver: Kociemba two-phase algorithm (Python), returns optimal solution in under a second
Frame & Couplers: Custom 3D printed (PETG), Fusion 360 designed

## Engineering 

1. Color Detection That Actually Works

Red and orange kept getting confused with fixed HSV thresholds. The fix was calibrating against the specific cube and lighting: white is detected by saturation (cutoff set between calibrated white and the least saturated color), and every other sticker is classified by circular hue distance to the calibrated reference hues. This correctly handles red wrapping around 0/180 in HSV, which finally separated red from orange reliably. (more detail in journal)

2. 6 Motor Coordination Over Serial

The ESP32 receives a full move sequence (e.g. R U2 D' B D' L2 F') as one string over Serial, parses it move by move, and executes each one including double turns and inverses by reversing direction or doubling the step count. Each motor also has its own tunable step count since print tolerances varied slightly across the 6 couplers. (more detail in journal)

3. The Coupler Design (4 Iterations Deep)

Connecting a 5mm D shaft motor to a Rubik's cube center cap was harder than expected. The final design is a hex peg that seats into the center cap hole, with a D bore and set screw on the motor side. It took 4 print iterations since PETG shrinks about 0.1 to 0.2mm from the CAD dimensions, so each version had to compensate to get a snug fit. (more detail in journal)

4. Phone Camera Over HTTPS

Browsers block camera access on non-HTTPS connections, so the Flask app runs with an ad hoc SSL context, letting the phone connect over the local network and stream camera frames to the server for processing. (more detail in journal)

## Schematic

<img width="1104" height="597" alt="Schematic" src="https://github.com/user-attachments/assets/f76fce13-379c-4ea4-aba1-69ef20a0bbd6" />

## CAD 

Custom design 3d printed frame made in Fusion 360, 6 motor mounts (4 side + top + bottom), magnetic top piece for easy clip on and off for cube loading, and stepper motor connector that goes into the stepper motor shaft and connects to a hex design cube connector that turns the face. 

