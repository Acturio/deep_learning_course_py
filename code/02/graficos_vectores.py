import matplotlib.pyplot as plt
import numpy as np

def dibujar_vectores(vectores, colores=None):
    """
    Dibuja una lista de vectores 2D desde el origen, 
    con un grid gris semitransparente y las coordenadas (x, y) al final de cada vector.

    Parámetros:
        vectores (list): Lista de vectores, cada uno como [x, y] o (x, y).
        colores (list, opcional): Lista de colores (en formato 'r', '#00FF00', etc.)
                                  Debe tener la misma longitud que 'vectores'.
    """
    # Validación de los vectores
    if not vectores or not all(len(v) == 2 for v in vectores):
        raise ValueError("Debes proporcionar una lista de vectores de dos dimensiones.")
    
    # Validación de los colores (si se proporcionan)
    if colores is not None and len(colores) != len(vectores):
        raise ValueError("La lista de colores debe tener la misma longitud que la lista de vectores.")

    plt.figure(figsize=(6, 6))
    plt.grid(True, color='gray', alpha=0.3)
    plt.axhline(0, color='black', linewidth=0.4)
    plt.axvline(0, color='black', linewidth=0.4)

    # Si no se pasan colores, generar una paleta automática
    if colores is None:
        colores = plt.cm.viridis(np.linspace(0, 1, len(vectores)))

    max_val = 0
    for i, v in enumerate(vectores):
        x, y = v
        color = colores[i]
        max_val = max(max_val, abs(x), abs(y))

        plt.quiver(0, 0, x, y, angles='xy', scale_units='xy', scale=1, 
                   color=color, width=0.005)
        plt.scatter(x, y, color=color)
        plt.text(x + 0.1, y + 0.1, f'({x}, {y})', fontsize=11, color=color)

    plt.xlim(-max_val - 1, max_val + 1)
    plt.ylim(-max_val - 1, max_val + 1)
    plt.gca().set_aspect('equal', adjustable='box')
    plt.title("Vectores en el plano")
    plt.show()
