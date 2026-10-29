# Evaluación en vivo de dominios (lgbm-20261029)

| Métrica | Valor |
| --- | --- |
| Empresas evaluadas | 129 |
| Con dominio esperado (etiquetadas) | 64 |
| **Exactitud** (aciertos / con dominio) | **78.9 %** |
| Exactitud de marca (otra extensión cuenta) | 93.0 % |
| Cobertura (etiquetadas con dominio / etiquetadas) | 89.1 % |
| Exactitud en confirmados (41) | 87.8 % |
| Esperado entre los 3 primeros candidatos | 82.8 % |
| Línea base (nombre + .com) | 20.3 % |
| Errores de red | 0 |
| **Empresas nunca vistas por el modelo** (19) | exactitud **60.0 %**, marca 86.7 %, cobertura 78.9 % |

Estados: confirmado 83, probable 31, no encontrado 15

## Fallos en empresas etiquetadas

| Empresa | Esperado | ORKESTA | Estado | Top 3 |
| --- | --- | --- | --- | --- |
| Alpura | alpura.com | alpura.com.mx | probable | alpura.com.mx, alpura.com, alpura.mx |
| Bell Flavors & Fragrances LATAM | bellff.com | — | no encontrado | — |
| CROWN México | crowncork.com / crowncork.com.mx | crown.mx | probable | crown.mx, crown.com |
| ElringKlinger México | elringklinger.com / elringklinger.de | elringklinger.com.mx | confirmado | elringklinger.com.mx, elringklinger.de |
| ENGEL de México S.A. de C.V. | engelglobal.com | engel.com | probable | engel.com |
| Farmacia San Pablo | farmaciasanpablo.com.mx | — | no encontrado | pablo.com.mx |
| General Motors de México | gm.com / gmfinancial.com.mx / chevrolet.com.mx | gm.com.mx | confirmado | gm.com.mx, gm.com, generalmotors.mx |
| Grupo Bafar | bafar.com.mx / grupobafar.com | grupobafar.mx | probable | grupobafar.mx, bafar.mx, grupobafar.com |
| GRUPO_NUTEC | nutec.com / nutecbickley.com | gruponutec.com | confirmado | gruponutec.com, nutec.com, nutec.com.mx |
| Grupo Urrea | urrea.com / urrea.com.mx | urrea.mx | confirmado | urrea.mx, urrea.com.mx, urrea.com |
| Kavak.com | kavak.com | kavak.mx | probable | kavak.mx, kavak.com, kavak.com.mx |
| LG México | lg.com / lge.com | — | no encontrado | lgmexico.com.mx, lg.com.mx |
| Mattel Latin America | mattel.com | — | no encontrado | mla.com.mx |
| MG México | mgmotor.com.mx / mgmotor.mx | — | no encontrado | — |
| O-I México | o-i.com | — | no encontrado | oi.com.mx |
| Otis Elevator Company México & Centro América | otis.com | — | no encontrado | — |
| Samsung Electronics Mexico | samsung.com | samsung.com.mx | confirmado | samsung.com.mx, samsung.com |
| TCL Electronics México | tcl.com | tcl.com.mx | probable | tcl.com.mx, tcl.mx, tclelectronics.com |
| Toyota Tsusho Corporation de Mexico | toyota-tsusho.com | toyota.mx | probable | toyota.mx |

## Empresas sin etiqueta: dominio propuesto para revisión

| Empresa | ORKESTA | Estado | Puntuación |
| --- | --- | --- | --- |
| Armstrong Laboratorios de México S.A. de C.V. | armstrong.com.mx | confirmado | 94 |
| Asofarma de Mexico | adium.com.mx | probable | 92 |
| Avigrupo | avigrupo.com.mx | confirmado | 94 |
| Blindajes Global Armor México | blindajesarmor.com.mx | confirmado | 98 |
| BYD CLEBER | bydcleber.com | confirmado | 99 |
| La Castellana | lacastellana.com | confirmado | 96 |
| Chirey México | chirey.mx | probable | 94 |
| CLEBER | cleber.com | probable | 90 |
| Coca-Cola Corporación del Fuerte | — | no encontrado | 0 |
| Laboratorios Columbia S.A. de C.V. | columbia.com | probable | 94 |
| Daimler Buses México | — | no encontrado | 0 |
| Detergentes y Jabones SASIL | — | no encontrado | 0 |
| Diken International | dikeninternational.com.mx | probable | 88 |
| Embotelladora AGA del Centro | embotelladoraaga.com | probable | 98 |
| Envases | envases.com.mx | confirmado | 99 |
| ETIMEX | etimex.com | confirmado | 99 |
| Eurotecsa | eurotecsa.com | confirmado | 99 |
| Fashion Group | fashiongroup.mx | confirmado | 99 |
| Franslux | franslux.com | confirmado | 98 |
| GarantiPLUS México | garantiplus.com | confirmado | 97 |
| Geely Auto Mexico | geely.com | probable | 83 |
| Ginga Group | ginga.com.mx | confirmado | 88 |
| GPS Global Packaging Supplier, S.A. de C.V. | — | no encontrado | 0 |
| Grupak | grupak.com.mx | confirmado | 99 |
| Grupo AGC | grupoagc.com.mx | confirmado | 95 |
| Grupo Autopolis. | autopolis.com.mx | confirmado | 86 |
| Grupo Continental | grupocontinental.mx | probable | 95 |
| Grupo Excelencia | grupoexcelencia.mx | confirmado | 98 |
| Grupo Loeffler-Russek | loeffler.com.mx | probable | 70 |
| Grupo Morsa de México | morsa.com.mx | confirmado | 91 |
| Grupo Pan de Pala | — | no encontrado | 0 |
| Grupo Corporativo Papelera | grupopapelera.com | probable | 78 |
| GWM Mexico | gwm.mx | confirmado | 96 |
| Haitian International México | haitian.mx | confirmado | 99 |
| Impro Aerospace México | — | no encontrado | 0 |
| Industria Farmacéutica Andrómaco S.A. de C.V. | andromaco.mx | confirmado | 95 |
| Industrias Lavin | thelavinagency.com.mx | confirmado | 97 |
| ISUZU Motors de México | isuzu.com | probable | 88 |
| Lactalis México | lactalis.com.mx | confirmado | 99 |
| Levbeth Medical, S.A. de C.V. | levbethmedical.com | confirmado | 99 |
| Litoplas | litoplas.com.mx | confirmado | 95 |
| Meddi | meddi.com.mx | confirmado | 99 |
| The Nearshore Company | thenearshorecompany.com | confirmado | 98 |
| Nissin Foods De México, S.A. De C.V. | nissinfoods.com | confirmado | 95 |
| Pinturas Prisa | prisa.mx | confirmado | 98 |
| Plastiexports | plastiexports.com | probable | 78 |
| PM Steele® | pmsteele.com.mx | confirmado | 99 |
| Poliuretanos Summa Woodbridge | — | no encontrado | 0 |
| ProAutomation | proautomation.com.mx | confirmado | 98 |
| PROCARGA | procarga.mx | confirmado | 99 |
| Prodynamics | prodynamics.com.mx | confirmado | 97 |
| LABORATORIOS QUÍMICA SON´S | quimicasons.mx | confirmado | 98 |
| Schwabe Pharma México | schwabe.com | probable | 85 |
| SEGAMAC SA de CV | segamac.com | confirmado | 98 |
| SINBIOTIK S.A. DE C.V | sinbiotik.com.mx | confirmado | 98 |
| SINCRONIA MEDICA APLICADA | sincroniamedicaaplicada.com | confirmado | 99 |
| Laboratorios Sophia | sophialab.com | confirmado | 88 |
| SUPRALUX | supralux.mx | confirmado | 98 |
| Termiz "Soluciones integrales en vapor y agua caliente" | — | no encontrado | 0 |
| Terramar Brands Corporativo | terramarbrands.com | confirmado | 79 |
| Ulsatech | ulsatech.com.mx | confirmado | 99 |
| Ultra Laboratorios | ultra.com.mx | probable | 73 |
| Unicar Plastics | unicarplastics.com | confirmado | 98 |
| Weser Pharma | weserpharma.com.mx | confirmado | 92 |
| Yadatex | yadatex.com | probable | 97 |
