import cv2
import requests
import json
import time
from datetime import datetime
import pygame
import threading
from typing import Optional, Dict, Any, List
import logging
from dataclasses import dataclass

# ============= CONFIGURACIÓN =============
API_BASE_URL = "https://fastapi-production-b6bb.up.railway.app"  # URL actualizada
CAMERA_INDEX = 1  # Índice de la cámara (0 para cámara principal)

# Configuración de sonidos (opcional)
SOUND_SUCCESS = "success.wav"  # Archivo de sonido para éxito
SOUND_ERROR = "error.wav"      # Archivo de sonido para error
SOUND_WARNING = "warning.wav"  # Archivo de sonido para advertencias

# Configuración de logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('qr_scanner.log'),
        logging.StreamHandler()
    ]
)

@dataclass
class QRValidation:
    """Clase para manejar la respuesta de validación de QR"""
    valid: bool
    message: str
    qr_data: Optional[Dict[str, Any]] = None
    accion: Optional[str] = None  # "ENTRADA", "SALIDA", "COMPLETADO"
    empleado_id: Optional[int] = None
    empleado_info: Optional[Dict[str, Any]] = None
    previous_scans: Optional[List[str]] = None

@dataclass
class ScanResult:
    """Clase para manejar el resultado del escaneo"""
    success: bool
    message: str
    empleado_id: Optional[int] = None
    empleado_info: Optional[Dict[str, Any]] = None
    is_first_scan: Optional[bool] = None  # True = Entrada, False = Salida
    fecha_escaneo: Optional[str] = None
    accion: Optional[str] = None

class QRScanner:
    def __init__(self):
        self.cap = None
        self.detector = cv2.QRCodeDetector()
        self.last_scanned_qr = ""
        self.last_scan_time = 0
        self.scan_cooldown = 3  # Segundos entre escaneos del mismo QR
        self.sound_enabled = True
        self.running = False
        self.current_display_info = None
        self.info_display_time = 0
        self.info_duration = 5  # Segundos para mostrar información
        
        # Inicializar pygame para sonidos (opcional)
        try:
            pygame.mixer.init()
            logging.info("✅ Sistema de sonido inicializado")
        except Exception as e:
            logging.warning(f"⚠️ No se pudo inicializar el sistema de sonido: {e}")
            self.sound_enabled = False
    
    def play_sound(self, sound_type: str):
        """Reproduce sonidos según el resultado de la validación"""
        if not self.sound_enabled:
            return
        
        try:
            sound_files = {
                "success": SOUND_SUCCESS,
                "error": SOUND_ERROR,
                "warning": SOUND_WARNING
            }
            
            sound_file = sound_files.get(sound_type)
            if sound_file:
                pygame.mixer.music.load(sound_file)
                pygame.mixer.music.play()
        except Exception as e:
            logging.warning(f"⚠️ Error reproduciendo sonido {sound_type}: {e}")
    
    def validate_qr_api(self, qr_id: str) -> QRValidation:
        """Valida el QR usando la API actualizada"""
        try:
            url = f"{API_BASE_URL}/qr/{qr_id}/validate"
            headers = {
                'Accept': 'application/json',
                'Content-Type': 'application/json'
            }
            response = requests.get(url, headers=headers, timeout=10)
            
            logging.info(f"🔍 Validando QR {qr_id} - Status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                
                validation = QRValidation(
                    valid=data.get("valid", False),
                    message=data.get("message", ""),
                    qr_data=data.get("qr_data"),
                    accion=data.get("accion", "UNKNOWN"),
                    empleado_id=data.get("empleado_info", {}).get("id") if data.get("empleado_info") else None,
                    empleado_info=data.get("empleado_info"),
                    previous_scans=[]  # La nueva API no devuelve esto en validate
                )
                
                logging.info(f"✅ Validación exitosa: {validation.accion} para empleado {validation.empleado_id}")
                return validation
                
            else:
                logging.error(f"❌ Error en API: Status {response.status_code} - {response.text}")
                return QRValidation(
                    valid=False,
                    message=f"Error de API: {response.status_code}",
                    accion="ERROR"
                )
                
        except requests.exceptions.Timeout:
            logging.error("⏰ Timeout conectando con la API")
            return QRValidation(
                valid=False,
                message="Timeout: No se pudo conectar con el servidor",
                accion="ERROR"
            )
        except requests.exceptions.ConnectionError:
            logging.error("🔌 Error de conexión con la API")
            return QRValidation(
                valid=False,
                message="Error: No se pudo conectar con el servidor",
                accion="ERROR"
            )
        except Exception as e:
            logging.error(f"❌ Error validando QR: {e}")
            return QRValidation(
                valid=False,
                message=f"Error inesperado: {str(e)}",
                accion="ERROR"
            )
    
    def record_scan_api(self, qr_id: str) -> ScanResult:
        """Registra el escaneo en la API actualizada"""
        try:
            url = f"{API_BASE_URL}/qr/{qr_id}/scan"
            headers = {
                'Accept': 'application/json',
                'Content-Type': 'application/json'
            }
            response = requests.post(url, headers=headers, timeout=10)
            
            logging.info(f"📝 Registrando escaneo QR {qr_id} - Status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                
                # La nueva API devuelve una estructura diferente
                result = ScanResult(
                    success=True,
                    message="Escaneo registrado exitosamente",
                    empleado_id=data.get("empleado_id"),
                    empleado_info=data.get("empleado_info"),
                    is_first_scan=data.get("es_entrada", True),  # True = Entrada, False = Salida
                    fecha_escaneo=data.get("hora_entrada") if data.get("es_entrada") else data.get("hora_salida"),
                    accion="ENTRADA" if data.get("es_entrada") else "SALIDA"
                )
                
                logging.info(f"✅ Escaneo registrado: {result.accion} para empleado {result.empleado_id}")
                return result
                
            else:
                error_detail = "Error desconocido"
                try:
                    error_data = response.json()
                    error_detail = error_data.get("detail", f"Status {response.status_code}")
                except:
                    error_detail = f"Status {response.status_code}"
                
                logging.error(f"❌ Error registrando escaneo: {error_detail}")
                return ScanResult(
                    success=False,
                    message=error_detail
                )
                
        except Exception as e:
            logging.error(f"❌ Error registrando escaneo: {e}")
            return ScanResult(
                success=False,
                message=f"Error: {str(e)}"
            )
    
    def get_display_color(self, accion: str, success: bool = True) -> tuple:
        """Obtiene el color para mostrar según la acción"""
        if not success:
            return (0, 0, 255)  # Rojo para error
        
        if accion == "ENTRADA" or accion == "ENTRADA_REGISTRADA":
            return (0, 255, 0)  # Verde para entrada
        elif accion == "SALIDA" or accion == "SALIDA_REGISTRADA":
            return (0, 255, 255)  # Amarillo para salida
        elif accion == "COMPLETADO":
            return (255, 0, 255)  # Magenta para ya completado
        else:
            return (0, 0, 255)  # Rojo para error/desconocido
    
    def format_display_info(self, validation: QRValidation = None, scan_result: ScanResult = None) -> list:
        """Formatea la información para mostrar en pantalla"""
        info_lines = []
        
        if scan_result and scan_result.success:
            # Mostrar resultado del escaneo exitoso
            info_lines.append(f"✅ {scan_result.accion} REGISTRADA")
            
            if scan_result.empleado_info:
                emp_info = scan_result.empleado_info
                info_lines.append(f"👤 {emp_info.get('name', f'Empleado {scan_result.empleado_id}')}")
                info_lines.append(f"📧 {emp_info.get('email', 'N/A')}")
                info_lines.append(f"🏢 {emp_info.get('role', 'N/A')}")
            
            if scan_result.fecha_escaneo:
                try:
                    fecha_dt = datetime.fromisoformat(scan_result.fecha_escaneo.replace('Z', '+00:00'))
                    info_lines.append(f"🕐 {fecha_dt.strftime('%H:%M:%S')}")
                except:
                    info_lines.append(f"🕐 {scan_result.fecha_escaneo}")
            
            info_lines.append(f"📊 ID: {scan_result.empleado_id}")
            
        elif validation:
            # Mostrar información de validación
            if validation.valid:
                if validation.accion == "COMPLETADO":
                    info_lines.append("⚠️ YA COMPLETO HOY")
                else:
                    info_lines.append(f"✅ PREPARADO: {validation.accion}")
                
                if validation.empleado_info:
                    emp_info = validation.empleado_info
                    info_lines.append(f"👤 {emp_info.get('name', f'Empleado {validation.empleado_id}')}")
                    info_lines.append(f"📧 {emp_info.get('email', 'N/A')}")
                    info_lines.append(f"🏢 {emp_info.get('role', 'N/A')}")
                
                info_lines.append(validation.message)
            else:
                info_lines.append("❌ QR INVÁLIDO")
                info_lines.append(validation.message)
        
        return info_lines
    
    def process_qr(self, qr_id: str) -> Optional[Dict[str, Any]]:
        """Procesa un QR escaneado con la nueva lógica"""
        current_time = time.time()
        
        # Verificar cooldown para evitar múltiples escaneos rápidos del MISMO QR
        if (qr_id == self.last_scanned_qr and 
            current_time - self.last_scan_time < self.scan_cooldown):
            return None  # No actualizar display durante cooldown
        
        self.last_scanned_qr = qr_id
        self.last_scan_time = current_time
        
        logging.info(f"🔍 QR escaneado: {qr_id}")
        
        # PASO 1: Validar el QR para saber qué acción se realizará
        validation = self.validate_qr_api(qr_id)
        
        if not validation.valid:
            logging.error(f"❌ QR inválido: {validation.message}")
            self.play_sound("error")
            return {
                "type": "validation",
                "data": validation,
                "success": False
            }
        
        # PASO 2: Si ya completó entrada y salida, mostrar mensaje pero no hacer nada más
        if validation.accion == "COMPLETADO":
            logging.info(f"⚠️ QR ya completó entrada y salida hoy: {qr_id}")
            self.play_sound("warning")
            return {
                "type": "validation",
                "data": validation,
                "success": True
            }
        
        # PASO 3: Registrar el escaneo (entrada o salida)
        scan_result = self.record_scan_api(qr_id)
        
        if not scan_result.success:
            logging.error(f"❌ Error al registrar escaneo: {scan_result.message}")
            self.play_sound("error")
            return {
                "type": "scan_error",
                "data": scan_result,
                "success": False
            }
        
        # PASO 4: Escaneo exitoso
        self.play_sound("success")
        logging.info(f"✅ {scan_result.accion} registrada exitosamente para empleado {scan_result.empleado_id}")
        
        return {
            "type": "scan_success",
            "data": scan_result,
            "success": True
        }
    
    def initialize_camera(self) -> bool:
        """Inicializa la cámara"""
        try:
            self.cap = cv2.VideoCapture(CAMERA_INDEX)
            
            if not self.cap.isOpened():
                logging.error(f"❌ No se pudo abrir la cámara {CAMERA_INDEX}")
                return False
            
            # Configurar resolución para mejor rendimiento
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            self.cap.set(cv2.CAP_PROP_FPS, 30)
            
            logging.info("✅ Cámara inicializada correctamente")
            return True
            
        except Exception as e:
            logging.error(f"❌ Error inicializando cámara: {e}")
            return False
    
    def run(self):
        """Ejecuta el bucle principal del escáner"""
        if not self.initialize_camera():
            print("❌ Error: No se pudo inicializar la cámara")
            return
        
        self.running = True
        print("🚀 Escáner QR iniciado. Presiona 'q' para salir, 's' para alternar sonido")
        print("=== Control de Asistencia con Entrada y Salida ===")
        print("- Primer escaneo del día: ENTRADA")
        print("- Segundo escaneo del día: SALIDA") 
        print("- Un registro por día por empleado")
        print("- Integrado con API FastAPI actualizada")
        print("================================================")
        
        try:
            while self.running:
                ret, frame = self.cap.read()
                
                if not ret:
                    logging.error("❌ Error capturando frame de la cámara")
                    break
                
                # Detectar códigos QR
                data, bbox, _ = self.detector.detectAndDecode(frame)
                
                if data:
                    # Procesar el QR y actualizar display si es nuevo
                    result = self.process_qr(data)
                    if result:  # Solo actualizar si no es cooldown
                        self.current_display_info = result
                        self.info_display_time = time.time()
                
                # Dibujar el bbox si se detectó un QR
                if bbox is not None:
                    bbox = bbox.astype(int)
                    cv2.polylines(frame, [bbox], True, (255, 0, 255), 2)
                
                # Mostrar información del último QR procesado
                if (self.current_display_info and 
                    time.time() - self.info_display_time < self.info_duration):
                    
                    result_data = self.current_display_info
                    
                    if result_data["type"] == "scan_success":
                        scan_result = result_data["data"]
                        color = self.get_display_color(scan_result.accion, True)
                        info_lines = self.format_display_info(scan_result=scan_result)
                    elif result_data["type"] == "validation":
                        validation = result_data["data"]
                        color = self.get_display_color(validation.accion, validation.valid)
                        info_lines = self.format_display_info(validation=validation)
                    else:  # scan_error
                        scan_result = result_data["data"]
                        color = self.get_display_color("ERROR", False)
                        info_lines = [f"❌ ERROR: {scan_result.message}"]
                    
                    # Fondo semi-transparente para mejor legibilidad
                    overlay = frame.copy()
                    cv2.rectangle(overlay, (10, 10), (500, 30 + len(info_lines) * 25), (0, 0, 0), -1)
                    cv2.addWeighted(overlay, 0.8, frame, 0.2, 0, frame)
                    
                    # Mostrar información línea por línea
                    for i, line in enumerate(info_lines):
                        y_pos = 35 + i * 25
                        cv2.putText(frame, line, (15, y_pos), 
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
                
                # Mostrar instrucciones
                cv2.putText(frame, "Escaner QR - Control Entrada/Salida (API Actualizada)", 
                            (10, frame.shape[0] - 80), 
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
                cv2.putText(frame, f"API: {API_BASE_URL.split('//')[-1]}", 
                            (10, frame.shape[0] - 55), 
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
                cv2.putText(frame, "Presiona 'q' para salir, 's' para sonido", 
                            (10, frame.shape[0] - 30), 
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
                
                # Mostrar estado del sonido
                sound_status = "ON" if self.sound_enabled else "OFF"
                cv2.putText(frame, f"Sonido: {sound_status}", (frame.shape[1] - 120, 30), 
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, 
                            (0, 255, 0) if self.sound_enabled else (0, 0, 255), 2)
                
               # Mostrar hora actual de Ecuador
                ecuador_time = datetime.now(ZoneInfo("America/Guayaquil"))
                current_time_str = ecuador_time.strftime('%H:%M:%S')
                cv2.putText(frame, current_time_str, (frame.shape[1] - 120, 60), 
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
                
                cv2.imshow('Escáner QR - Control Entrada/Salida', frame)
                
                # Manejar teclas
                key = cv2.waitKey(1) & 0xFF
                if key == ord('q'):
                    break
                elif key == ord('s'):
                    self.sound_enabled = not self.sound_enabled
                    status = "activado" if self.sound_enabled else "desactivado"
                    print(f"🔊 Sonido {status}")
                    logging.info(f"🔊 Sonido {status}")
        
        except KeyboardInterrupt:
            logging.info("⚠️ Escáner interrumpido por el usuario")
        except Exception as e:
            logging.error(f"❌ Error en el bucle principal: {e}")
        finally:
            self.cleanup()
    
    def cleanup(self):
        """Limpia los recursos"""
        self.running = False
        
        if self.cap:
            self.cap.release()
            logging.info("📷 Cámara liberada")
        
        cv2.destroyAllWindows()
        
        if self.sound_enabled:
            try:
                pygame.mixer.quit()
                logging.info("🎵 Mezclador de sonido cerrado")
            except:
                pass
        
        logging.info("✅ Escáner cerrado correctamente")

def check_api_connection():
    """Verifica la conexión con la API actualizada"""
    try:
        print(f"🔍 Verificando conexión con {API_BASE_URL}...")
        
        # Verificar endpoint de salud
        health_response = requests.get(f"{API_BASE_URL}/health", timeout=5)
        if health_response.status_code == 200:
            health_data = health_response.json()
            print(f"✅ API saludable: {health_data.get('status', 'UNKNOWN')}")
            print(f"📊 Versión: {health_data.get('version', 'N/A')}")
            
            # Mostrar métricas si están disponibles
            if 'metrics' in health_data:
                metrics = health_data['metrics']
                print(f"📈 Total QRs: {metrics.get('total_qrs', 0)}")
                print(f"📊 Total escaneos: {metrics.get('total_escaneos', 0)}")
        
        # Verificar endpoint de información
        info_response = requests.get(f"{API_BASE_URL}/info", timeout=5)
        if info_response.status_code == 200:
            info_data = info_response.json()
            print(f"🏢 App: {info_data.get('app', 'QR Attendance API')}")
            
            if 'attendance_stats' in info_data:
                stats = info_data['attendance_stats']
                print(f"👥 Empleados registrados: {stats.get('empleados_registrados', 0)}")
                print(f"📅 Escaneos hoy: {stats.get('escaneos_hoy', 0)}")
            
            # Verificar integración con backend
            if 'backend_integration' in info_data:
                backend = info_data['backend_integration']
                print(f"🔗 Backend NestJS: {backend.get('status', 'UNKNOWN')}")
                print(f"👥 Empleados en backend: {backend.get('total_employees_in_backend', 0)}")
        
        print("✅ Conexión con API exitosa")
        return True
        
    except requests.exceptions.ConnectionError:
        print(f"❌ Error de conexión: No se puede conectar a {API_BASE_URL}")
        print("   Verifica que la API esté ejecutándose y accesible")
        return False
    except requests.exceptions.Timeout:
        print(f"⏰ Timeout: {API_BASE_URL} no responde")
        return False
    except Exception as e:
        print(f"❌ Error inesperado verificando API: {e}")
        return False

def main():
    """Función principal"""
    print("=== Escáner QR - Sistema de Control de Entrada y Salida ===")
    print("🔄 Versión actualizada para API FastAPI con integración NestJS")
    print("Verificando conexión con la API...")
    
    if not check_api_connection():
        print("\n⚠️  No se pudo conectar con la API.")
        print("   Asegúrate de que la API esté ejecutándose y accesible.")
        print(f"   URL configurada: {API_BASE_URL}")
        print("\n🛠️  Para cambiar la URL, edita la variable API_BASE_URL en el código")
        return
    
    print(f"\n🚀 Iniciando escáner...")
    print("📱 Listo para escanear códigos QR")
    print("🎯 Los registros se sincronizarán automáticamente con el dashboard web")
    
    scanner = QRScanner()
    try:
        scanner.run()
    except Exception as e:
        logging.error(f"❌ Error ejecutando scanner: {e}")
        print(f"❌ Error ejecutando scanner: {e}")

if __name__ == "__main__":
    main()