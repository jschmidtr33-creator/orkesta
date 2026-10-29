"""Apellidos frecuentes en LATAM y España (forma plegada).

Sirven para distinguir ``jperez`` (inicial + apellido) de un buzón genérico de una palabra
(``proveedores@``): un patrón inicial+apellido con apellido reconocido es evidencia normal; sin
reconocerlo, evidencia débil. No necesita ser exhaustiva.
"""

from __future__ import annotations

COMMON_SURNAMES: frozenset[str] = frozenset(
    """
    garcia rodriguez gonzalez fernandez lopez martinez sanchez perez gomez martin jimenez ruiz
    hernandez diaz moreno munoz alvarez romero alonso gutierrez navarro torres dominguez vazquez
    ramos gil ramirez serrano blanco molina morales suarez ortega delgado castro ortiz rubio
    marin sanz nunez iglesias medina garrido cortes castillo santos lozano guerrero cano prieto
    mendez cruz calvo gallego vidal leon marquez herrera pena flores cabrera campos vega fuentes
    carrasco diez caballero reyes nieto aguilar pascual santana herrero lorenzo montero hidalgo
    gimenez ibanez ferrer duran santiago benitez mora vicente vargas arias carmona crespo roman
    pastor soto saez velasco moya soler parra esteban bravo gallardo rojas pardo merino franco
    espinosa izquierdo lara rivas silva rivera casado arroyo redondo camacho rey vera otero luque
    galan montes rios sierra segura carrillo marcos marti soriano mendoza contreras salazar
    aguirre estrada guzman figueroa espinoza valdez sandoval cardenas acosta pacheco miranda
    rosales villa avila ochoa juarez maldonado zamora trujillo velazquez cervantes cordova
    salinas valencia orozco ayala nava lugo meza escobar bautista padilla zapata villalobos
    beltran galindo arellano barrera chavez rangel ponce pineda leyva solis cuevas paredes
    quintero bermudez cifuentes osorio restrepo henao zuluaga arango londono jaramillo cardona
    gaviria betancur montoya ospina giraldo valencia mejia rendon velez echeverri uribe correa
    quiroga quispe mamani huaman condori chavez flores palacios villanueva salas tapia araya
    munoz rojas soto contreras sepulveda morales fuentes valenzuela tapia figueroa carrasco
    gallardo pizarro cortes olivares alvarado vergara bustos navarrete jara riquelme saavedra
    farias aravena caceres molina bravo vasquez leiva toro donoso urrutia benavides peralta
    barrios cabello lagos pereira oliveira costa souza ferreira almeida carvalho gomes lima
    """.split()
)
