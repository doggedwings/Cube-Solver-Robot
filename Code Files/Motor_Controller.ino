// Motor 1 - Bottom - Down
#define D_STEP 16
#define D_DIR  17
#define D_EN   18

// Motor 2 - Side - Front
#define F_STEP 19
#define F_DIR  21
#define F_EN   22

// Motor 3 - Side - Left
#define L_STEP 23
#define L_DIR  25
#define L_EN   26

// Motor 4 - Side - Right
#define R_STEP 12
#define R_DIR  13
#define R_EN   14

// Motor 5 - Side - Back
#define B_STEP 27
#define B_DIR  32
#define B_EN   33

// Motor 6 - Top - Up
#define U_STEP 4
#define U_DIR  5
#define U_EN   15

#define STEP_DELAY_US  800
#define MOVE_PAUSE_MS  300

int stepsPerFace[] = {53, 50, 50, 50, 50, 50}; // D, F, L, R, B, U

int stepPins[] = {D_STEP, F_STEP, L_STEP, R_STEP, B_STEP, U_STEP};
int dirPins[]  = {D_DIR,  F_DIR,  L_DIR,  R_DIR,  B_DIR,  U_DIR};
int enPins[]   = {D_EN,   F_EN,   L_EN,   R_EN,   B_EN,   U_EN};

void setup() {
  delay(2000);
  Serial.begin(115200);
  for (int i = 0; i < 6; i++) {
    pinMode(stepPins[i], OUTPUT);
    pinMode(dirPins[i],  OUTPUT);
    pinMode(enPins[i],   OUTPUT);
    digitalWrite(enPins[i],   LOW);
    digitalWrite(dirPins[i],  HIGH);
    digitalWrite(stepPins[i], LOW);
  }
  Serial.println("Ready — waiting for solve command...");
}

void rotateFace(int faceIndex, int dirPin, bool clockwise) {
  digitalWrite(dirPin, clockwise ? HIGH : LOW);
  delayMicroseconds(5);
  int steps = stepsPerFace[faceIndex];
  int stepPin = stepPins[faceIndex];
  for (int i = 0; i < steps; i++) {
    digitalWrite(stepPin, HIGH);
    delayMicroseconds(STEP_DELAY_US);
    digitalWrite(stepPin, LOW);
    delayMicroseconds(STEP_DELAY_US);
  }
}

void moveU(bool cw = true) { rotateFace(5, U_DIR, !cw); }
void moveD(bool cw = true) { rotateFace(0, D_DIR, !cw); }
void moveF(bool cw = true) { rotateFace(1, F_DIR, !cw); }
void moveB(bool cw = true) { rotateFace(4, B_DIR, !cw); }
void moveL(bool cw = true) { rotateFace(2, L_DIR, !cw); }
void moveR(bool cw = true) { rotateFace(3, R_DIR, !cw); }

void moveU2() { moveU(); moveU(); }
void moveD2() { moveD(); moveD(); }
void moveF2() { moveF(); moveF(); }
void moveB2() { moveB(); moveB(); }
void moveL2() { moveL(); moveL(); }
void moveR2() { moveR(); moveR(); }

void executeMove(String move) {
  move.trim();
  Serial.println(move);
  if      (move == "U")  moveU();
  else if (move == "U'") moveU(false);
  else if (move == "U2") moveU2();
  else if (move == "D")  moveD();
  else if (move == "D'") moveD(false);
  else if (move == "D2") moveD2();
  else if (move == "F")  moveF();
  else if (move == "F'") moveF(false);
  else if (move == "F2") moveF2();
  else if (move == "B")  moveB();
  else if (move == "B'") moveB(false);
  else if (move == "B2") moveB2();
  else if (move == "L")  moveL();
  else if (move == "L'") moveL(false);
  else if (move == "L2") moveL2();
  else if (move == "R")  moveR();
  else if (move == "R'") moveR(false);
  else if (move == "R2") moveR2();
  else Serial.println("Unknown move: " + move);
}

void loop() {
  if (Serial.available()) {
    String line = Serial.readStringUntil('\n');
    line.trim();
    if (line.length() == 0) return;
    Serial.println("Received: " + line);
    Serial.println("--- Solving ---");
    int start = 0;
    while (start < (int)line.length()) {
      int sp = line.indexOf(' ', start);
      String move;
      if (sp == -1) {
        move = line.substring(start);
        start = line.length();
      } else {
        move = line.substring(start, sp);
        start = sp + 1;
      }
      if (move.length() > 0) {
        executeMove(move);
        delay(MOVE_PAUSE_MS);
      }
    }
    Serial.println("=== Solve Complete! ===");
  }
}
