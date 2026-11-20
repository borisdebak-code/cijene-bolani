"""Kategorije kao u katalozima/web trgovinama (struktura prema Konzumovom
katalogu kategorija, bez sezonskih skupina) i razvrstavanje proizvoda.

Službeni cjenici imaju samo grube kategorije (Hrana, Pića, Kozmetika...) ili
nikakve, pa proizvod razvrstavamo po nazivu: pravila se provjeravaju redom,
prvo pogođeno pravilo određuje (kategorija, potkategorija). Grube kategorije
iz cjenika služe kao ograda: npr. proizvod koji je trgovac označio kao
kozmetiku ne može završiti među hranom.
"""
from __future__ import annotations

import re

from .adapters.csvutil import strip_accents

# (kod, naziv, [(kod potkategorije, naziv)])
CATEGORIES: list[tuple[str, str, list[tuple[str, str]]]] = [
    ("voce", "Voće i povrće", [("voce", "Voće"), ("povrce", "Povrće"), ("suho", "Suho voće")]),
    ("mlijecni", "Mliječni proizvodi i jaja", [
        ("mlijeko", "Mlijeko"), ("jogurti", "Jogurti i kefiri"), ("sirevi", "Sirevi i pripravci"),
        ("vrhnje", "Vrhnja"), ("deserti", "Mliječni deserti"), ("maslac", "Maslac, margarin, mast"),
        ("jaja", "Jaja")]),
    ("meso", "Meso i riba", [
        ("piletina", "Piletina i puretina"), ("svinjetina", "Svinjetina, junetina, ostalo"),
        ("riba", "Riba i plodovi mora")]),
    ("delikatesa", "Delikatesa", [
        ("hrenovke", "Hrenovke i kobasice"), ("suhomesnato", "Šunka, salame, pršut")]),
    ("pekarnica", "Kruh i peciva", [
        ("kruh", "Kruh"), ("peciva", "Peciva i lisnato"), ("dvopek", "Dvopek i tost"),
        ("kolaci", "Kolači i torte"), ("tijesta", "Tijesta, kvasci, mrvice")]),
    ("pica", "Pića", [
        ("voda", "Voda"), ("gazirana", "Gazirana pića"), ("sokovi", "Sokovi i negazirana pića"),
        ("energetska", "Energetska pića"), ("pivo", "Pivo"), ("vino", "Vino"),
        ("alkohol", "Alkoholna pića")]),
    ("zamrznuto", "Zamrznuta hrana", [
        ("sladoledi", "Sladoledi i deserti"), ("pizze", "Pizze"), ("riba", "Riba i plodovi mora"),
        ("povrce", "Voće i povrće"), ("jela", "Jela i tijesto")]),
    ("slatkisi", "Slatkiši i grickalice", [
        ("keksi", "Keksi"), ("cokolade", "Čokolade"), ("bombonijere", "Bombonijere"),
        ("snackovi", "Snackovi i pločice"), ("bomboni", "Bomboni, lizalice, žvakaće"),
        ("cips", "Čips, flips, kokice"), ("stapici", "Štapići, pereci, krekeri"),
        ("orasasto", "Kikiriki i orašasto")]),
    ("dorucak", "Kave, čajevi, pahuljice, namazi", [
        ("kava", "Kave"), ("caj", "Čajevi"), ("kakao", "Čokoladni napitci"),
        ("pahuljice", "Pahuljice i muesli"), ("marmelade", "Marmelade, džemovi, med"),
        ("cokonamazi", "Čokoladni namazi"), ("pastete", "Paštete i namazi")]),
    ("priprema", "Priprema jela i kolača", [
        ("ulja", "Ulja"), ("ocat", "Ocat"), ("brasno", "Brašno i krupice"), ("secer", "Šećer i sladila"),
        ("sol", "Sol"), ("zacini", "Začini i mješavine"), ("umaci", "Umaci"),
        ("kecap", "Kečap, senf, majoneza"), ("rajcica", "Proizvodi od rajčice"),
        ("kolaci", "Za kolače")]),
    ("tjestenina", "Tjestenina, riža, žitarice", [
        ("tjestenina", "Tjestenina i njoki"), ("riza", "Riža"), ("tortilje", "Tortilje"),
        ("mahunarke", "Mahunarke i žitarice")]),
    ("konzerve", "Konzervirano i juhe", [
        ("riblje", "Riblje konzerve"), ("povrce", "Povrće"), ("juhe", "Juhe"), ("kompoti", "Kompoti"),
        ("ajvar", "Ajvar, kiselo, masline"), ("gotova", "Mesne konzerve i gotova jela")]),
    ("djeca", "Dječji svijet", [
        ("pelene", "Pelene"), ("hrana", "Dječja hrana"), ("njega", "Dječja njega")]),
    ("higijena", "Njega i higijena", [
        ("papir", "Toaletni papir"), ("ulosci", "Ulošci i intimna njega"),
        ("maramice", "Rupčići i maramice"), ("sapuni", "Sapuni i gelovi za tuširanje"),
        ("dezodoransi", "Dezodoransi i parfemi"), ("kosa", "Njega kose"), ("zubi", "Njega zubi"),
        ("kreme", "Kreme i losioni"), ("brijanje", "Brijanje i depilacija"), ("sminka", "Šminka")]),
    ("ciscenje", "Čišćenje i pranje", [
        ("posudje", "Pranje posuđa"), ("rublje", "Deterdženti za rublje"), ("omeksivaci", "Omekšivači"),
        ("sredstva", "Sredstva za čišćenje"), ("pribor", "Pribor za čišćenje")]),
    ("kucanstvo", "Kućanske potrepštine", [
        ("papirnati", "Ubrusi i salvete"), ("folije", "Folije i vrećice"), ("svijece", "Svijeće i šibice"),
        ("osvjezivaci", "Osvježivači prostora"), ("insekticidi", "Insekticidi i repelenti"),
        ("baterije", "Baterije i žarulje")]),
    ("ljubimci", "Kućni ljubimci", [("psi", "Psi"), ("macke", "Mačke"), ("ostali", "Ostali ljubimci")]),
]

NONFOOD = {"djeca", "higijena", "ciscenje", "kucanstvo", "ljubimci"}

# riječi koje označavaju prerađeni proizvod (ne svježe voće/povrće)
_PROCESSED = (r"kuglof|madelein|komp\b|nap\b|prot\b|francusk|delikates|restan|tuba\b|ekol|obrok|kas\b|vh\b|dekorat|set\b|cookie|cjedil|knedl|bresaol|bazen|sjeme\b|ribana|pate\b|vafel|pice|napit|hidra|pouch|\d+[,.]?\d* ?l\b|\d+ ?ml\b|sok|nektar|jogurt|dzem|marmelad|cokolad|sladoled|caj|bombon|keks|okus|aroma|sirup|kasic"
              r"|kompot|desert|puding|liker|rakij|pivo|cips|grickal|zitaric|musli|pahulj|smoothie|voda|napit"
              r"|gumen|pita|torta|kolac|strudel|savijac|krem\b|krema|ocat|losion|sampon|gel|sapun|deterdz|osvjez|svije"
              r"|suh|susen|u soku|konzerv|juha|umak|ajvar|kisel|ukiseljen|u octu|sterilizir|pasiran|koncentrat"
              r"|kecap|ketchup|zacin|vegeta|sjemenk|ulje|pire|prah|dzus|smrzn|zamrzn|salat[ae] od|namaz|pasteta"
              r"|bar |plocic|cips|flips|krekeri|tortilj|kasa|zele\b|muffin|croissant|kroasan|maska|parfem|svijec")

FROZEN = r"smrzn|zamrzn|duboko zamrz|ledo\b|frikom|frozy|perla\b|smrz\b|hortex"
FISH = (r"riba\b|ribe\b|ribl|losos|oslic|bakalar|srdel|skus|orada|brancin|pastrv|kozic|lignj|hobotnic|dagnj"
        r"|skamp|plodovi mora|hek |file aljask|aljaski|sipa|surimi|stapici od ribe|riblji stap")

# (kategorija, potkategorija, obavezni uzorak, isključujući uzorak, dodatni obavezni uzorak)
_R = [
    # --- kućni ljubimci
    ("ljubimci", "psi", r"hrana psi|my pets|za ljubimce|wanpy|dog\b|za pse|za psa|psec|pedigree|chappi|frolic|dentastix|dog chow", None, None),
    ("ljubimci", "macke", r"za macke|za macku|macji|macje|macja|cat\b|whiskas|felix|sheba|kitekat|perfect fit|catsan|posip za macke|cat chow|gourmet gold", None, None),
    ("ljubimci", "ostali", r"ptice|papig|za ptice|za ribice|za glodav|za kunice|za hrcke|za kucne ljubimce|hrana za ribe", None, None),
    # --- dječji svijet
    ("djeca", "pelene", r"pelen|pampers|huggies|babylove|baby dry|pants za bebe", None, None),
    ("djeca", "njega", r"baby|bebe|djecj|kids|junior|bebi", None, r"sampon|krem|ulje|gel|losion|maramic|puder|pasta za zube|cetkic|kupka|sapun|pjena"),
    ("djeca", "hrana", r"kasic|hipp|bebivita|nutrilon|aptamil|humana|milupa|za bebe|dojenac|dojencad|pocetna hrana|frutolino|bebelac|nestle nan|nan optipro|djecja hrana", r"sampon|krema|losion|gel|ulje za|kupka|maramic|sjedalic|pidzam|pidz\b|bodi|cisc|frosch|pelen|kolut|hlac|odjec|carap|kapa|majic|haljin|cipel|tenisic|kompl", None),
    # --- njega i higijena
    ("higijena", "papir", r"toaletni papir|wc papir|toalet papir|toaletni pap", None, None),
    ("higijena", "ulosci", r"ulos|tampon|intimn|always|libresse|carefree|discreet|o b |inkontinenc|tena ", r"wc|kruti|obuc|cipel", None),
    ("higijena", "dezodoransi", r"dezodorans|deo |deo spray|antiperspirant|roll on|parfem|toaletna voda|eau de|body spray|mirisna voda", None, None),
    ("higijena", "kosa", r"sampon|regenerator|balzam za kosu|boja za kosu|lak za kosu|gel za kosu|pjena za kosu|maska za kosu|za kosu|serum za kosu|suhi sampon", r"susilo|uvijac|ravnal|cetka|gumica|ukosnic|figaro|\d+ ?w\b", None),
    ("higijena", "zubi", r"pasta za zube|zubna pasta|cetkica za zube|cetkice za zube|vodica za usta|zubni konac|colgate|sensodyne|aquafresh|oral b|elmex|parodontax|blend a med|zubn", None, None),
    ("higijena", "sapuni", r"sapun|gel tus|gel za tus|tus gel|kupka|gel za tusiranje|shower|pjena za kupanje|tekuci sapun", None, None),
    ("higijena", "brijanje", r"brijac|britvic|za brijanje|depilac|vosak za depil|gillette|after shave|poslije brijanja|nozic", None, None),
    ("higijena", "sminka", r"maskar|ruz za usne|olovka za oci|puder|lak za nokte|odstranjivac laka|skidanje sminke|micelarn|sjenilo|tus za oci", r"lezaljk|kisobran", None),
    ("higijena", "kreme", r"krema za|losion|mlijeko za tijelo|body lotion|body milk|zastitni faktor|spf|za suncanje|nakon suncanja|balzam za usne|njega lica|serum za lice|krema protiv|nivea|dove |garnier", None, None),
    ("higijena", "maramice", r"rupcic|papirnate maramice|vlazne maramice|maramice|stapici za usi|vatirani|blazinice|flaster", None, None),
    # --- čišćenje
    ("ciscenje", "posudje", r"za sudje|za sude|sudje|za posudje|posudje|tablete za perilicu|tablete za strojno|fairy|somat|finish |calgonit|sjaj za perilicu|sol za perilicu|za strojno pranje", r"strugac|line\b", None),
    ("ciscenje", "omeksivaci", r"omeksiv|lenor|silan|ornel|vernel", None, None),
    ("ciscenje", "rublje", r"deter\b|za rub\b|deterdz|za rublje|za pranje rublja|prasak za pranje|ariel|persil|faks|bonux|perwoll|kapsule za pranje|gel za pranje|odstranjivac mrlja|vanish|izbjeljivac rublja|tide", r"kosara|susilo|stalak|vjesalic", None),
    ("ciscenje", "sredstva", r"za ciscenje|cistac|domestos|ajax|cif |mr proper|savo|izbjeljiv|varikin|kamenc|wc gel|wc blok|wc osvjez|za staklo|za kupaon|odmascivac|bref|harpic|sredstvo za|za podove|za pecnicu|dezinfek|cillit", r"\d+ ?w\b|parni|regal|polic", None),
    ("ciscenje", "pribor", r"spuzv|krpa|krpe|metla|mop |rukavice|cetka za|brisac|lopatica za smece|zicana vuna|kanta za", None, None),
    # --- kućanske potrepštine
    ("kucanstvo", "papirnati", r"ubrus|salvet|papirnati rucnik|kuhinjski papir|papirnate case|papirnati tanjur", r"drzac", None),
    ("kucanstvo", "folije", r"folij|vrecic|vreca za smece|vrece za smece|papir za pecenje|masni papir|alu posud|kesa|vrecice za zamrz|za smece", None, None),
    ("kucanstvo", "svijece", r"svijec|lampion|zigic|sibic|upaljac|kresiv", None, None),
    ("kucanstvo", "osvjezivaci", r"osvjeziv|mirisni stapic|air wick|glade|ambi pur|miris za prostor|mirisna svijeca", None, None),
    ("kucanstvo", "insekticidi", r"insekt|repelent|protiv komaraca|protiv muha|raid|baygon|autan|protiv moljaca|mamac za", None, None),
    ("kucanstvo", "baterije", r"baterij|zarulj|duracell|varta|energizer|led zarul", r"bez baterij|vozilo|igrack", None),
    # --- pića
    ("pica", "energetska", r"ener\b|energetsk|energy|red bull|monster|burn\b|hell\b|guarana", r"plocic|bar |snack|munch|spray|osvjez|gumica|backe|mix\b|miks|kolo|truck|gel\b|coffee|kava|baby", None),
    ("pica", "pivo", r"pivo|piva\b|radler|ozujsko|karlovacko|heineken|lasko|tomislav|staropramen|tuborg|carlsberg|corona|holsten|budweiser|velebitsko|stella artois|bavaria|beck s|paulaner|leffe|kozel|pilsner|lager|shandy", r"ocat|kvasac|sapun|sampon|cips|prazna", None),
    ("pica", "vino", r"vino|vina |pjenusac|prosecco|sampanj|grasevin|malvazij|plavac|merlot|cabernet|chardonnay|traminac|teran|portugizac|frankovk|pinot|sauvignon|zinfandel|crljenak|babic|posip|rose |muskat", r"ocat|vinsk|grozdje|pecivo|kruh|hot dog", None),
    ("pica", "alkohol", r"rakij|liker|vodk|votk|gin |whisk|viski|rum |konjak|brandy|pelinkovac|travaric|sljivovic|jagermeister|tequila|aperol|vermut|martini|amaro|vinjak|medica|orahovac|maraskin|biska|loza|baileys|cognac|absint", r"cokolad|bombon|praline|kolac|klomp|vel\b", None),
    ("pica", "gazirana", r"cola\b|pepsi|fanta|sprite|schweppes|tonic|gaziran|cockta|7up|mirinda|orangina|ginger|pipi |bitter lemon", r"bombon|bomb\b|gum\b|zele\b|lizalic|gumen|bojank|voda\b|sok\b|cokolad", None),
    ("pica", "voda", r"\bvoda\b|mineraln|jamnica|jana |studena|cetina|aqua viva|izvorsk|sensation|kisela voda|romerquelle|evian|san benedetto|perrier", r"mic\b|micel|toaletn|za usta|za glacanje|destilir|parfem|cvjetna|pidzam", None),
    ("pica", "sokovi", r"\bsok|sokovi|nektar|juice|ledeni caj|ice tea|cedevita|limunad|sirup|negaziran|smoothie|izoton|cijedjen|nestea|fuze tea|cappy|pago|vocni napitak|bistri|multivitamin napitak", r"bombon|zele\b|jogurt|sladoled|kolac|flips|smoki|cips", None),
    # --- zamrznuto
    ("zamrznuto", "sladoledi", r"sladoled|slad\b|ice cream|kornet|magnum|snjeguljic|cornetto|calippo|sorbet|smrznuti desert", None, None),
    ("zamrznuto", "pizze", r"pizza|pizze", r"tijesto za|umak za|zacin|flips|bruschett|okus|slanin|cips|krupic|kecap|tjestenin|kreker|ribani|snack|rings|brasn|mix\b", None),
    ("zamrznuto", "riba", FROZEN, None, FISH),
    ("zamrznuto", "povrce", FROZEN, r"savijac|strudl|kolac|pita",  r"povrc|grasak|mahune|brokul|cvjetac|spinat|mrkv|kukuruz|voce|jagod|borovnic|malin|mjesavin|krumpir|pomfrit|kroket|gljiv|tikvic|paprik"),
    ("zamrznuto", "jela", FROZEN, None, None),
    ("zamrznuto", "povrce", r"pomfrit|pommes frites|krokete|smrznut", None, None),
    # --- konzervirano
    ("konzerve", "riblje", r"eva tuna|eva losos|eva riba|eva sardin|delamaris|tuna|tunj|sardin|srdela .* ulj|srdele u|skusa u|losos u|incun|ribl[ja] pasteta|riblja konzerva|file skuse|sardele", r"sendvi|tramez|pizza|lisnat|wanpy|cat\b", None),
    ("konzerve", "juhe", r"juha|juhe|krem juha|temeljac|kocka za juhu|bujon|instant juha|ramen|consomme", None, None),
    ("konzerve", "kompoti", r"kompot|u soku|u sirupu|breskve u|ananas u|vocna salata u", None, None),
    ("konzerve", "ajvar", r"ajvar|pindjur|kisel[ie] |ukiseljen|feferon|u octu|tursij|maslin[ae]|kapari|zimnic|cvikl|salata od cikle|kiseli krastav", r"ulje|ocat|bombon|gumi|zele\b|lizalic|keks|kreker|grisini|cips", None),
    ("konzerve", "gotova", r"mesni dorucak|lunch meat|gotovo jelo|gulas|sarma|punjene paprike|cufte|mesni narezak|konzervirano meso|hasche|grah s kobas|varivo|cobanac|bolonjez", r"umak|zacin|juha|fant\b|junetin|svinjetin|za gulas|mjesavin", None),
    ("konzerve", "povrce", r"grasak|grah|kukuruz|leca\b|slanutak|mahune|povrc|gljive|sampinjon", r"fant\b|zacin|mjesavin za|orasast|przen|cips|flips|kokic|skus|riba|tuna|baguette|kruh",  r"konzerv|limenk|steriliz|kuhan|salamur|secerac|staklenk|u vlastitom|\d+ ?g "),
    # --- delikatesa
    ("delikatesa", "hrenovke", r"hrenovk|frankfurt|virsl|kobasic|kob\b|cevapc|kranjsk|debrecin|cabanossi|cajna kobas", r"za kobasice|zacin|za pse|za macke|lisnat|pecivo|burek|pizza", None),
    ("delikatesa", "suhomesnato", r"sunk|salam|prsut|slanin|panceta|kulen|mortadel|zimska|pecenic|carsko meso|dimljen|naresc|narezak|suho meso|pileca prsa u ovitku|kare|buncek|vratina|pecenica|speck|bacon|kobasica", r"pizza|cips|zacin|pasteta|paprika|vegeta|sir\b|pice|puzic|losos|pivo", None),
    # --- slatkiši i grickalice
    ("slatkisi", "keksi", r"keks|cajno pecivo|cajni\b|vafel|biskvit|kolacic|napolitank|oreo|petit beurre|jaffa|domacic|cookies|wafer|vafl|medenjac|pisking|ljesnjak keks|leibniz|tuc |prinzen", r"za pse|za macke|torta|mrvice|model|whey|protein", None),
    ("slatkisi", "bombonijere", r"bombonijer|pralin|merci|raffaello|ferrero rocher|mon cheri|kinder schoko bons|toffifee|asortiman cokolad|desert cokolad", None, None),
    ("slatkisi", "cips", r"cips|chips|pringles|smoki|flips|kokice|popcorn|tortilla chips|nachos|kukuruzni snack|cipsi|chio|lays|ruffles", None, None),
    ("slatkisi", "snackovi", r"plocic|snack|protein bar|snickers|mars |twix|bounty|kitkat|kit kat|kinder bueno|corny|musli plocic|kinder country|duplo|knoppers|milky way|lion |kinder maxi|kinder delice|pingui|kinder joy|kinder jaje", r"cips|krekeri|smoki", None),
    ("slatkisi", "cokolade", r"cokolad|milka|dorina|lindt|ritter sport|toblerone|kinder cokolad|nestle classic|gorka|tamna cokolada|mlijecna cokolada", r"puding|desert|napit|namaz|mlijeko|kakao|sladoled|jogurt|torta|kolac|keks|zitaric|pahulj|musli|preljev|liker|kava|caj|za kuhanje|kocke za|prelje|mrvic|ukras", None),
    ("slatkisi", "bomboni", r"bombon|lizalic|zvakac|zvake|gumeni|gumene|zelk|haribo|mentos|orbit|tic tac|zele |karamel|zele bomb|bombice|zubna guma|airwaves|stimorol|trident|ricola|halls|chupa", r"bombonijer|noodle|juha|bolsius|mirisn|lucic|kava|soja|napit|ml\b|kefir|preljev", None),
    ("slatkisi", "stapici", r"stapic|pereci|perece|krekeri|kreker|grisini|grissini|crackers|pretzel|slani stap|minicrackers|tuc ", r"za usi|od ribe|riblji|za zube|hig|vat|jaje|gnojiv|ledeni|vanilij|za grill|mesa|harry|potter|areon|mirisn", None),
    ("slatkisi", "orasasto", r"kikiriki|\borah|\borasi|badem|ljesnjak|indijski orah|pistaci|mjesavina orasast|studentska|makadamij|brazilski orah|pekan", r"ulje|namaz|cokolad|keks|nutella|mlijeko|napitak|plocic|kolac|torta|zitaric|vafel|punj|cokolino|kukuruz|nap\b|bilj|jogurt|puding|rahatlokum|ploc|palacink|klijest|cannol|profiter|orahnjac|makovnj|kokos orah", None),
    # --- kave, čajevi, pahuljice, namazi
    ("dorucak", "kava", r"\bkava|\bkave\b|espresso|cappuccino|nescafe|jacobs|barcaffe|franck|lavazza|illy|kapsul|instant kava|zrno|3u1|3in1|kafa|dolce gusto|nespresso|kaffee", r"liker|bombon|cokolad|sladoled|puding|aparat|\bcaj\b", None),
    ("dorucak", "kakao", r"kakao|cokoladni napit|benquick|nesquik|ovomaltin|topla cokolada|instant napitak|cacao|kakao napit|cokolino", r"kolac|preljev|keks|plocic|cokolada mlijecna|dvopek|vafel|jogurt|pahulj|cokoladic|croissant|crois|kroasan|indijsk|orasc|napolitank|rolad|naranc|prali|muffin|donut|protein", None),
    ("dorucak", "caj", r"\bcaj\b|\bcaja\b|\bcaju\b|cajevi|teekanne|lipton|sipovac|kamilic|metvica caj|sumsko voce caj|zeleni caj|crni caj|vocni caj|travni caj|filter vrecic|biljni caj", r"ledeni|ice tea|nestea|fuze", None),
    ("dorucak", "pahuljice", r"pahulj|muesli|musli|cornflakes|corn flakes|zitarice za dorucak|granola|zobene|chocapic|cini minis|fitness|frosties|crunchy|lion zitarice|coco pops|kasa |zobena kasa|krupne zob", r"plocic|pl\b|bar |kruh|keks|jogurt|trake|traka|\d+ ?cm", None),
    ("dorucak", "marmelade", r"marmelad|dzem\b|dzemovi|pekmez|\bmed\b|meda\b|cvjetni med|bagremov|livadni med|kestenov med|vocni namaz|lekvar", r"medenjac|medvjed|medic|medjimur|medium|volan", None),
    ("dorucak", "cokonamazi", r"nutella|cokoladni namaz|lino lada|namaz od ljesnjak|choco krem|kakao krem|ljesnjak krem|cokoladna krema|namaz od lj", None, None),
    ("dorucak", "pastete", r"pastet|namaz|argeta|humus|hummus|tuna namaz|jetrena", r"za lice|za tijelo|cokolad|ljesnjak|nutella|maslac", None),
    # --- mliječni proizvodi i jaja
    ("mlijecni", "jogurti", r"jogurt|kefir|acidofil|kiselo mlijeko|probioti|\bab mlijeko|lgg|skyr|grcki|actimel|activia|b aktiv|jogurtni napitak|pitki jogurt|tekuci jogurt|ayran", r"za lice|maska|kolac|keks|cokolad|bombon|torta|preljev|smoothie bar", None),
    ("mlijecni", "vrhnje", r"vrhnj|mileram|cooking cream|creme fraiche|slag u spreju|kiselo vrhnje|vrhnje za kuhanje|biljno vrhnje|slatko vrhnje", r"sladoled|torta|kolac|juha|umak|puding|rings|cips|flips|maslac", None),
    ("mlijecni", "deserti", r"puding|mlijecni desert|monte\b|dukatela|panna cotta|rizin pudin|griz na mlijeku|cokoladni desert|kinder pingui|danette|mlijecna rezina|kinder mlijecna", r"u prahu|prasak|za kuhanje|vrecic|pice|alkohol|kolac|pustolov", None),
    ("mlijecni", "maslac", r"maslac|margarin|\bmast\b|svinjska mast|ghee|maslo\b|butter\b|becel|rama\b|dobro jutro|biljni namaz", r"kikiriki|keks|kolac|cokolad|kikiriki maslac|maslac od kikirik|za kosu|za tijelo|kakao maslac|croissant|kroasan|riza|pecivo|na maslacu|s maslacem|bonbon|bombon|tost", None),
    ("mlijecni", "sirevi", r"\bsir\b|\bsira\b|sirevi|sirni|gauda|gouda|edamer|mozzarel|parmez|feta|trapist|ementaler|cheddar|mascarpone|ricotta|skut|krem sir|topljeni|grana padano|camembert|brie|halloumi|zdenka|gorgonzola|tilsit|paski|svjezi sir|cottage|mlinarski sir|sir za pizzu|gauda|polutvrdi|tvrdi sir|meki sir|ribani sir|listici sira|babybel|kraljevski sir|president", r"tjest|jastucic|sirup|sirovi|sirok|krekeri|cips|pizza|burek|pita|tjestenina|umak|tortelini|tortellini|ravioli|sendvic|kroasan|pecivo|zacin|stapic", None),
    ("mlijecni", "jaja", r"\bjaja\b|\bjaje\b|jaja l |jaja m |jaja s ", r"boj|ukras|plasti|kinder|cokol|tanjur|rezac|kalup|posud|rezanci|tjestenin|majonez|liker|uskr|usk\b|lizalic|bombon|kuhalo|cetk|dekor|nosac|zlic|pjena|metal|fi\b|fi\d|na stapicu|presli|motiv|mot\b|stolic|ratan|stiropor|ker\b|traka|mjerac|vre\b|set jaja|spatzle|\d+ ?cm|\d+ ?mm", None),
    ("mlijecni", "mlijeko", r"\bmlijeko\b|\bmlijeka\b|\bmlij\b|\bmilk\b|zobeni napitak|bademov napitak|sojin napitak|rizin napitak|biljni napitak|mlijecni napitak|trajno mlij|svjeze mlij", r"zamj|nan\b|rolino|draz|not milk|pillows|jastu|lino\b|krem\b|kremom|milka|bonbon|cokolino|kasa|sun\b|ecran|zastit|spf|snit|za tijelo|za sunc|za lice|cokolad[ae]\b|kokos|zamjensk|za djecu|dojen|cist|kasic|pocetn|keks|kolac|sapun|krema|kupka|u prahu|bombon|cokoladica|pahulj|zitaric", None),
    # --- kruh i peciva
    ("pekarnica", "tijesta", r"kvasac|lisnato tijesto|tijesto za pizzu|tijesto za|kore za|mlinci\b|krusne mrvice|mrvice|vlasci|tijesto za strudlu|jufke|kore\b", None, None),
    ("pekarnica", "dvopek", r"dvopek|\btost\b|toast|prepecen|tostirani kruh|bruschett", r"za kosu|sir za tost", None),
    ("pekarnica", "kolaci", r"kolac|torta|torte|muffin|rolad|sacher|strudl|savijac|pita od|krafn|cupcake|brownie|biskvitni kolac|kremsnit|baklav|tiramisu", r"za kolace|smjesa|prasak|ukras|kalup|papir|preljev|mrvic|puding|sladoled|keks|tortel|kosaric|muffine", None),
    ("pekarnica", "peciva", r"pecivo|peciva|kifl|lepinj|pogac|burek|kroasan|croissant|lisnat|zemick|perec|kajzeric|pita s|pita sa|zitna pecica|mini pizza|sendvic|panini|hot dog peciv|hamburger peciv|brioche", r"za pecivo|prasak", None),
    ("pekarnica", "kruh", r"\bkruh|baguette|bageta|ciabatta|polubijel|razeni|crni kruh|bijeli kruh|kukuruzni kruh|integralni kruh|tostni kruh|hljeb|somun|pinca|kruh od", r"mrvice|smjesa za|za kruh|brasno|krusne", None),
    # --- priprema jela i kolača
    ("priprema", "kolaci", r"prasak za pecivo|vanilin|vanilij|puding u prahu|smjesa za|preljev za|ukrasi za|zelatin|sol jelena|kakao za|\bslag\b|aroma za|cokolada za kuhanje|mrvice cokoladne|secer u prahu|ukrasni secer|fondant|glazura|krema za kolace|kvasac", None, None),
    ("priprema", "rajcica", r"pasirana rajcic|koncentrat rajcic|rajcica pelat|pelati|passata|sjeckana rajcic|polpa|pire od rajcic|rajcica u komad", None, None),
    ("priprema", "kecap", r"kecap|ketchup|senf|majonez|tartar|aioli|\bhren\b|remulad|barbecue sauce|bbq umak", None, None),
    ("priprema", "umaci", r"umak|umaci|pesto|bolognese|\bsos\b|sauce|dresing|preliv za salat|sojin umak|worcester|tabasco|chili sauce|salsa", r"ribl|sendvic|tjestenina s|pizza|makaron|tjestenin|spaget|klepe|njoki|konzerv", None),
    ("priprema", "zacini", r"zacin|vegeta|papar|origano|bosiljak|lovor|\bkim\b|cimet|klincic|curry|kurkum|mjesavina za|kotanyi|cili\b|chilli|majcina dusica|ruzmarin|persin susen|bijeli luk granul|mljevena paprika|slatka paprika|ljuta paprika|muskatni|djumbir mljev|podravka zacin|fant\b|marinada", r"wc|mirisn", None),
    ("priprema", "ulja", r"\bulje|\bulja\b|maslinov|suncokretov|repicin|bucin|kokosovo ulje|ulje od", r"u ulju|za tijelo|za kosu|za masazu|za bebe|motorno|eterican|za suncanje|za lice|lavand|esencijal|proair|aroma|raspr", None),
    ("priprema", "ocat", r"\bocat|aceto|balzamic|jabucni ocat|vinski ocat|alkoholni ocat", None, None),
    ("priprema", "brasno", r"brasn|krupic|\bgriz|palent|kukuruzna krupica|heljdino brasno|pirovo brasno|gustin|skrob", r"pecivo|kruh|keks|kolac", None),
    ("priprema", "secer", r"secer|sladilo|stevij|saharin|eritritol|ksilitol|smedji secer|kristal secer|fruktoz|melasa|agavin sirup|javorov sirup", r"bez secera|bez dodanog|bombon|cokolad|keks|zele|sok|napitak|kava|caj", None),
    ("priprema", "sol", r"\bsol\b|\bsoli\b|morska sol|kuhinjska sol|himalajska sol|jodirana sol", r"sol jelena|sol za perilicu|kupka|za kupanje|sol za kupku|naocal", None),
    # --- tjestenina, riža, žitarice
    ("tjestenina", "riza", r"\briza\b|\brize\b|riza |basmati|jasmin|rizot|risott|arborio|dugo zrno|integralna riza", r"rizin napitak|rizine|krekeri|vafl|kolac|puding|ulje|balm|melem", None),
    ("tjestenina", "tortilje", r"tortilj|tortilla|\bwrap|pita kruh|pita tijesto|tacos", r"chips|cips|nachos|vent\b", None),
    ("tjestenina", "tjestenina", r"tjestenin|spaghetti|spageti|spagete|penne|fusilli|makaron|rezanc|lazanj|lasagne|tagliatelle|farfalle|puzici|fide|noodles|tortel|ravioli|njoki|gnocchi|kus kus|couscous|pasta |rigatoni|linguine|fettuccine|orzo|tagliolini|krpice|valjusci|svjeza tjestenina", r"pasta za zube|umak|zacin|juha", None),
    ("tjestenina", "mahunarke", r"\bleca|slanutak|\bgrah|\bsoja|kvinoja|quinoa|heljda|proso|jecam|bulgur|amarant|chia|lanene sjemenk|sjemenke|zitarice|pir\b|ovsene|zob\b", r"napitak|plocic|keks|kruh|pahulj|musli|kolac|kasic|papir|krumpir", None),
    # --- meso i riba
    ("meso", "riba", FISH, r"konzerv|u ulju|ulju\b|u umaku|pasteta|namaz|za macke|za pse|hrana za|cips|stapici od rakov|ulje|surimi|nood|instant|juha|dodatak|salata|pommes|porcij|enjoy", None),
    ("meso", "piletina", r"piletin|pileci|pilece|pileca|\bpile\b|puretin|pureci|purece|pureca|zabatak|batak|pileca prsa|pileca krila|purica|kokos meso|cijelo pile", r"juha|zacin|kocka|pasteta|hrenovk|kobasic|kob\b|sunka|salama|cips|hrana za|nood|instant|rezanc|posebna|salata s|list pile|umjetn|svile|sm\b|wanpy|adult", None),
    ("meso", "svinjetina", r"svinjetin|svinjsk|junetin|juneci|junece|juneca|govedin|goved|teletin|telec|janjetin|janjec|mljeveno meso|mljevena junet|cevapc|pljeskavic|odrezak|kotlet|\bbut\b|\bvrat\b|rebra\b|rebarc|ombolo|buncek|meso za|\bmeso\b|biftek|ramstek|lungic|kremenadl|gulas meso|svinjski|jeten", r"juha|zacin|kocka|pasteta|hrenovk|kobasic|sunka|salama|cips|hrana za|konzerv|umak|pizza|burek|dorucak|narezak|bez mesa|zamjena za meso|vegan|nood|instant|psi\b|paprikas", None),
    # --- voće i povrće
    ("voce", "suho", r"\bsuh[eaoi]|susen|grozdic|datul|suhe smokve|suhe sljive|suhe marelice|brusnice susen|kandiran|goji", r"meso|kobasic|sir|kruh|kvasac|sampon|hrana za|juha|tjestenin|suho voce i orasast", None),
    ("voce", "voce", r"banan|jabuk|krusk|naranc|mandarin|limun|grejp|\bkivi|ananas|grozdj|jagod|borovnic|malin|lubenic|dinj|breskv|nektarin|marelic|sljiv|tresnj|visnj|avokad|mango|smokv|\bkaki\b|\bnar\b|klementin|limet|kupin|ribiz|papaj|marakuj|pomelo|liči|lici", _PROCESSED, None),
    ("voce", "povrce", r"krumpir|rajcic|paprik|krastav|\bluk\b|\bluka\b|crveni luk|cesnjak|\bmrkv|kupus|kelj|zelena salata|salata ledenka|kristal salata|salata\b|tikvic|patlidzan|brokul|cvjetac|spinat|blitva|mahune|celer|persin|rotkvic|cikla|bundev|gljiv|sampinjon|poriluk|rikol|batat|djumbir|radic|kozjak|kukuruz klip|grah mahun|motovilac|matovilac|komoracc|komorac|sparog|artic|endivij|kiselica|mladi luk|vlasac", _PROCESSED + r"|pomfrit|kroket|smrzn|zamrzn|cips|grickal", None),
]

# predmeti (posuđe, igračke, odjeća, aparati...) – nikad u kategorijama hrane i pića
NONFOOD_WORDS = re.compile(r"(?<![a-z0-9])(?:" + "|".join([
    r"pekac", r"kalup", r"hlac", r"kostim", r"kupac", r"grudnjak", r"ogrtac", r"klomp", r"bolsius", r"naocal", r"gnojiv", r"raspr", r"filter slim", r"filter za", r"set za", r"kuhac", r"licil", r"smoking", r"pahuljica zlatna", r"posud", r"tava\b", r"lonac", r"zdjel", r"casa\b", r"case\b", r"tanjur", r"zlic",
    r"vilic", r"noz\b", r"rezac", r"presa\b", r"ribez", r"cetk", r"igrack", r"figur", r"lego\b", r"puzzle",
    r"slagalic", r"pidzam", r"carap", r"majic", r"kapa\b", r"rukavic", r"torba", r"ruksak", r"kisobran",
    r"japank", r"bikini", r"papuc", r"biljez", r"blok\b", r"mapa\b", r"olovk", r"bojic", r"knjig",
    r"slikovnic", r"naljepnic", r"dekoracij", r"aranzman", r"buket", r"lampic", r"kabel", r"punjac",
    r"aparat\b", r"uredaj", r"friteza", r"mikser", r"blender", r"kuhalo", r"toster", r"grijac", r"sisac",
    r"susilo", r"odvijac", r"pila\b", r"vozilo", r"lutka", r"plis", r"privjes", r"navlak", r"omotnic",
    r"sjedalic", r"cigaret", r"nosac", r"doza za", r"posudic", r"kutija za", r"drzac", r"stalak", r"pinceta",
    r"list ubodne", r"korektor", r"korektur", r"wunder baum", r"pjescanik", r"kosilic", r"prazna boca",
    r"boca\b", r"pvc\b", r"uskrsn", r"uskr\b", r"ukrasn", r"lampion", r"svijec", r"forma za", r"kosaric",
    r"podloga", r"podmetac", r"krpa", r"spuzv", r"alat", r"rolo\b", r"vrecica za", r"cajnik", r"dzezva",
    r"mlinac", r"termos", r"spremnik", r"jastuk", r"deka\b", r"rucnik", r"pregac", r"frozen\b", r"disney",
    r"minnie", r"mickey", r"paw patrol", r"barbie", r"hot wheels", r"playstation", r"nintendo",
]) + ")")

def _rx(p: str | None):
    # svaka alternativa mora počinjati na početku riječi ("rum" ne smije pogoditi "forum")
    return re.compile(r"(?<![a-z0-9])(?:" + p + ")") if p else None


_COMPILED = [(c, s, _rx(inc), _rx(exc), _rx(req)) for c, s, inc, exc, req in _R]

_PRODUCE_BRANDS = re.compile(r"(?<![a-z0-9])(?:podravka|zvijezda|vitaminka|bonduelle|eva|hortex|ledo|frikom"
                             r"|garden good|natur pur|giana|mutti|cirio|zbregov|z bregov|dukat|polleo)")

_HINT_NONFOOD = ("kozmetik", "toaletne potrep", "sredstva za cisc", "proizvodi za kucanstvo")
_HINT_FOOD = ("hrana", "pica", "pice")


def _norm(s: str) -> str:
    s = strip_accents(s or "").lower()
    return " " + re.sub(r"[^a-z0-9%]+", " ", s).strip() + " "


def classify(name: str, brand: str | None = None, categories: str | None = None) -> str | None:
    """Vrati 'kategorija/potkategorija' ili None.

    categories: grube kategorije iz cjenika svih trgovaca (npr. "HRANA, Hrana").
    """
    t = _norm(f"{name} {brand or ''}")
    hint = _norm(categories or "")
    nonfood_hint = any(h in hint for h in _HINT_NONFOOD)
    food_hint = any(h in hint for h in _HINT_FOOD) and not nonfood_hint
    is_object = bool(NONFOOD_WORDS.search(t))
    for cat, sub, inc, exc, req in _COMPILED:
        if nonfood_hint and cat not in NONFOOD:
            continue
        if food_hint and cat in {"higijena", "ciscenje", "kucanstvo"}:
            continue
        if is_object and cat not in NONFOOD:
            continue
        if inc.search(t) and not (exc and exc.search(t)) and (req is None or req.search(t)):
            # svježe voće i povrće: naziv počinje voćem/povrćem ("Jagoda 300 g", "Mladi luk"),
            # a ne markom s okusom ("Fortia borovnica", "Kindii maramice jagoda")
            if cat == "voce" and sub in ("voce", "povrce"):
                head = " " + " ".join(_norm(name).split()[:2]) + " "
                if not inc.search(head) or _PRODUCE_BRANDS.search(t):
                    continue
            return f"{cat}/{sub}"
    return None


def tree() -> list[dict]:
    return [{"code": c, "name": n, "subs": [{"code": f"{c}/{s}", "name": sn} for s, sn in subs]}
            for c, n, subs in CATEGORIES]


# provjera: svako pravilo pokazuje na postojeću potkategoriju
_VALID = {f"{c}/{s}" for c, _, subs in CATEGORIES for s, _ in subs}
assert all(f"{c}/{s}" in _VALID for c, s, *_ in _R), [f"{c}/{s}" for c, s, *_ in _R if f"{c}/{s}" not in _VALID]
