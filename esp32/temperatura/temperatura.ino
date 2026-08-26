#include <OneWire.h>
#include <DallasTemperature.h>

#define PIN_SENSOR_1 16
#define PIN_SENSOR_2 17
#define PIN_SENSOR_3 18
#define PIN_SENSOR_4 19

const uint8_t NUM_SENSORES = 4;
const uint8_t RESOLUCION_BITS = 12;
const unsigned long TIEMPO_CONVERSION_MS = 750;
const unsigned long PERIODO_MUESTREO_MS = 1000;
const float TEMP_INVALIDA_85 = 85.0;
const float TOLERANCIA_85 = 0.05;
const uint8_t FALLOS_PARA_DESCONEXION = 3;

OneWire bus1(PIN_SENSOR_1);
OneWire bus2(PIN_SENSOR_2);
OneWire bus3(PIN_SENSOR_3);
OneWire bus4(PIN_SENSOR_4);

DallasTemperature sensor1(&bus1);
DallasTemperature sensor2(&bus2);
DallasTemperature sensor3(&bus3);
DallasTemperature sensor4(&bus4);

DallasTemperature* sensores[NUM_SENSORES] = {&sensor1, &sensor2, &sensor3, &sensor4};

float lecturas[NUM_SENSORES];
float ultimasValidas[NUM_SENSORES];
uint8_t fallosConsecutivos[NUM_SENSORES];

bool transmitiendo = true;
bool conversionEnCurso = false;
unsigned long marcaConversion = 0;
unsigned long marcaCiclo = 0;

void setup() {
  Serial.begin(115200);

  for (uint8_t i = 0; i < NUM_SENSORES; i++) {
    sensores[i]->begin();
    sensores[i]->setResolution(RESOLUCION_BITS);
    sensores[i]->setWaitForConversion(false);
    lecturas[i] = NAN;
    ultimasValidas[i] = NAN;
    fallosConsecutivos[i] = 0;
  }

  marcaCiclo = millis();
}

void loop() {
  procesarComandos();

  if (!transmitiendo) {
    return;
  }

  unsigned long ahora = millis();

  if (!conversionEnCurso && (ahora - marcaCiclo >= PERIODO_MUESTREO_MS)) {
    for (uint8_t i = 0; i < NUM_SENSORES; i++) {
      sensores[i]->requestTemperatures();
    }
    conversionEnCurso = true;
    marcaConversion = ahora;
    marcaCiclo = ahora;
    return;
  }

  if (conversionEnCurso && (ahora - marcaConversion >= TIEMPO_CONVERSION_MS)) {
    for (uint8_t i = 0; i < NUM_SENSORES; i++) {
      lecturas[i] = leerSensor(i);
    }
    conversionEnCurso = false;
    emitirTrama();
  }
}

float leerSensor(uint8_t indice) {
  float valor = sensores[indice]->getTempCByIndex(0);

  bool desconectado = (valor == DEVICE_DISCONNECTED_C);
  bool artefacto85 = (fabs(valor - TEMP_INVALIDA_85) < TOLERANCIA_85);

  if (desconectado || artefacto85) {
    if (fallosConsecutivos[indice] < FALLOS_PARA_DESCONEXION) {
      fallosConsecutivos[indice]++;
    }
    if (fallosConsecutivos[indice] >= FALLOS_PARA_DESCONEXION) {
      ultimasValidas[indice] = NAN;
      return NAN;
    }
    return ultimasValidas[indice];
  }

  fallosConsecutivos[indice] = 0;
  ultimasValidas[indice] = valor;
  return valor;
}

void emitirTrama() {
  float suma = 0.0;
  uint8_t validos = 0;

  for (uint8_t i = 0; i < NUM_SENSORES; i++) {
    if (!isnan(lecturas[i])) {
      suma += lecturas[i];
      validos++;
    }
  }

  if (validos > 0) {
    Serial.print(suma / validos, 2);
  } else {
    Serial.print("nan");
  }

  for (uint8_t i = 0; i < NUM_SENSORES; i++) {
    Serial.print(',');
    if (isnan(lecturas[i])) {
      Serial.print("nan");
    } else {
      Serial.print(lecturas[i], 2);
    }
  }

  Serial.println();
}

void procesarComandos() {
  if (!Serial.available()) {
    return;
  }

  String comando = Serial.readStringUntil('\n');
  comando.trim();
  comando.toUpperCase();

  if (comando == "PING") {
    Serial.println("PONG");
  } else if (comando == "START") {
    transmitiendo = true;
    conversionEnCurso = false;
    marcaCiclo = millis() - PERIODO_MUESTREO_MS;
  } else if (comando == "STOP") {
    transmitiendo = false;
    conversionEnCurso = false;
  }
}