/*
 * temperatura.ino
 * Sistema fotoacústico ICAT-UNAM — Zarco
 *
 * ESP32-C3 Super Mini · OneWire en GPIO4 · 4x DS18B20 waterproof · USB-CDC 115200 baud
 *
 * Trama de salida cada ~1 s:
 *   "25.30,1,1,0,1\n"
 *    └ temp_promedio °C · S1 · S2 · S3 · S4  (1 = presente, 0 = ausente)
 *
 * Comandos aceptados desde Python (enviar con '\n'):
 *   PING  →  responde "PONG\n"
 *   STOP  →  detiene transmisión (ESP32 permanece activo hasta reset)
 */

#include <OneWire.h>
#include <DallasTemperature.h>

#define ONE_WIRE_BUS  4          // GPIO4
#define NUM_SENSORES  4
#define RESOLUCION    12         // bits — tiempo de conversión ~750 ms
#define PAUSA_MS      250        // ms de pausa al final de cada ciclo

OneWire           oneWire(ONE_WIRE_BUS);
DallasTemperature sensores(&oneWire);

bool transmitiendo = true;

// ─────────────────────────────────────────────────────────────────────────────

void setup() {
    Serial.begin(115200);

    sensores.begin();
    sensores.setResolution(RESOLUCION);
    sensores.setWaitForConversion(true);   // bloquea ~750 ms durante la conversión
}

// ─────────────────────────────────────────────────────────────────────────────

void loop() {
    atenderComandos();                     // primera lectura: antes de la conversión

    if (!transmitiendo) {
        delay(100);
        return;
    }

    sensores.requestTemperatures();        // bloquea ~750 ms

    atenderComandos();                     // segunda lectura: captura STOP enviado
                                           // mientras esperaba la conversión

    float suma   = 0.0f;
    int   validos = 0;
    int   estado[NUM_SENSORES];

    for (int i = 0; i < NUM_SENSORES; i++) {
        float t = sensores.getTempCByIndex(i);

        // 85.0 °C = valor de power-on reset del DS18B20 (lectura corrupta)
        // DEVICE_DISCONNECTED_C = -127.0 °C
        bool ok = (t != DEVICE_DISCONNECTED_C) && (t != 85.0f);

        estado[i] = ok ? 1 : 0;

        if (ok) {
            suma += t;
            validos++;
        }
    }

    float promedio = (validos > 0) ? (suma / (float)validos) : 0.0f;

    if (transmitiendo) {
        Serial.print(promedio, 2);
        for (int i = 0; i < NUM_SENSORES; i++) {
            Serial.print(',');
            Serial.print(estado[i]);
        }
        Serial.println();
    }

    delay(PAUSA_MS);
}

// ─────────────────────────────────────────────────────────────────────────────

void atenderComandos() {
    while (Serial.available()) {
        String cmd = Serial.readStringUntil('\n');
        cmd.trim();

        if (cmd == "PING") {
            Serial.println("PONG");
        } else if (cmd == "STOP") {
            transmitiendo = false;
        }
    }
}
