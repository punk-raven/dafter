from __future__ import annotations

from dataclasses import dataclass

from .script import HINDI, Script
from .voice import caller_voice

ENGLISH = Script(
    turns=(
        "Hi, how are you doing today?",
        "How will the weather be today?",
        "Tell me an easy way to make masala chai.",
        "How far is Bangalore from Chennai?",
        "Can you suggest one good book to read?",
        "I'm not able to sleep at night, what should I do?",
        "How much water should one drink in a day?",
        "Tell me something about cricket.",
        "My phone has become very slow yaar.",
        "Suggest a nice game for kids.",
        "How do I wake up early in the morning?",
        "How do you say thank you in Hindi?",
        "Tell me one small joke.",
        "What should I wear in the monsoon?",
        "What are the benefits of doing yoga?",
        "I want to learn cooking.",
        "How do I book a train ticket?",
        "What is your favourite colour?",
        "How do I focus on my studies?",
        "Okay, thank you so much.",
    ),
    long_prompts=(
        "Tell me about the Taj Mahal in two sentences.",
        "How to take care of health in summer, tell me in two sentences.",
        "Tell me about any one Indian festival in two sentences.",
        "Give me two tips for good sleep.",
        "Say two sentences about Mumbai city.",
    ),
    interruptions=(
        "Wait, listen to me.",
        "One minute, I want to ask something else.",
        "No no, hold on.",
        "Listen, I have a question in between.",
        "Stop stop, I need to say something.",
    ),
    backchannels=("hmm", "okay", "yeah", "right", "mhm"),
    fillers=("um", "uh", "umm", "uh", "um"),
    paused=(
        ("I want to book", "a train ticket, how do I do it?"),
        ("My phone", "is running very slow."),
        ("How far is Bangalore", "from Chennai?"),
        ("At night I", "am not able to sleep, what should I do?"),
        ("How do I wake up", "early in the morning?"),
    ),
)

TELUGU = Script(
    turns=(
        "నమస్కారం, మీరు ఎలా ఉన్నారు?",
        "ఈ రోజు weather ఎలా ఉంటుంది?",
        "Simple గా tea ఎలా చేయాలో చెప్పండి.",
        "Hyderabad నుంచి Vijayawada ఎంత దూరం?",
        "ఒక మంచి book పేరు చెప్పండి.",
        "నాకు రాత్రి నిద్ర పట్టట్లేదు, ఏం చేయాలి?",
        "రోజుకి ఎన్ని నీళ్ళు తాగాలి?",
        "Cricket గురించి ఏదైనా చెప్పండి.",
        "నా phone చాలా slow అయిపోయింది.",
        "పిల్లల కోసం ఒక మంచి game చెప్పండి.",
        "పొద్దున్నే తొందరగా ఎలా లేవాలి?",
        "English లో ధన్యవాదాలు ఎలా చెప్తారు?",
        "ఒక చిన్న joke చెప్పండి.",
        "వర్షాకాలంలో ఏం వేసుకోవాలి?",
        "Yoga చేస్తే ఏం లాభాలు ఉన్నాయి?",
        "నాకు వంట నేర్చుకోవాలని ఉంది.",
        "Train ticket ఎలా book చేయాలి?",
        "మీకు ఇష్టమైన colour ఏది?",
        "చదువు మీద concentration ఎలా పెట్టాలి?",
        "సరే, చాలా thanks అండి.",
    ),
    long_prompts=(
        "Taj Mahal గురించి రెండు వాక్యాల్లో చెప్పండి.",
        "ఎండాకాలంలో health ఎలా చూసుకోవాలో రెండు వాక్యాల్లో చెప్పండి.",
        "మన దేశంలో ఏదైనా ఒక పండగ గురించి రెండు వాక్యాల్లో చెప్పండి.",
        "మంచి నిద్ర కోసం రెండు tips ఇవ్వండి.",
        "Hyderabad city గురించి రెండు వాక్యాలు చెప్పండి.",
    ),
    interruptions=(
        "ఆగండి, నా మాట వినండి.",
        "ఒక్క నిమిషం ఆగండి, ఇంకోటి అడగాలి.",
        "వద్దు వద్దు, కొంచెం ఆగండి.",
        "వినండి, మధ్యలో ఒక question ఉంది.",
        "ఆగు ఆగు, నేను ఒకటి చెప్పాలి.",
    ),
    backchannels=("ఊ", "సరే", "అవును", "అలాగే", "ఊం"),
    fillers=("అం", "ఉమ్మ్", "అం", "ఉమ్మ్", "అం"),
    paused=(
        ("నాకు train ticket", "book చేయాలి, ఎలా చేయాలి?"),
        ("నా phone", "చాలా slow గా ఉంది."),
        ("Hyderabad నుంచి Vijayawada", "ఎంత దూరం?"),
        ("నాకు రాత్రి", "నిద్ర పట్టట్లేదు, ఏం చేయాలి?"),
        ("పొద్దున్నే తొందరగా", "ఎలా లేవాలి?"),
    ),
)

KANNADA = Script(
    turns=(
        "ನಮಸ್ಕಾರ, ಹೇಗಿದ್ದೀರಾ?",
        "ಇವತ್ತು weather ಹೇಗಿರುತ್ತೆ?",
        "Simple ಆಗಿ tea ಹೇಗೆ ಮಾಡೋದು ಅಂತ ಹೇಳಿ.",
        "Bangalore ಇಂದ Mysore ಎಷ್ಟು ದೂರ?",
        "ಒಂದು ಒಳ್ಳೆ book ಹೆಸರು ಹೇಳಿ.",
        "ನನಗೆ ರಾತ್ರಿ ನಿದ್ದೆ ಬರ್ತಿಲ್ಲ, ಏನು ಮಾಡ್ಲಿ?",
        "ದಿನಕ್ಕೆ ಎಷ್ಟು ನೀರು ಕುಡೀಬೇಕು?",
        "Cricket ಬಗ್ಗೆ ಏನಾದ್ರೂ ಹೇಳಿ.",
        "ನನ್ನ phone ತುಂಬಾ slow ಆಗಿದೆ.",
        "ಮಕ್ಕಳಿಗೆ ಒಂದು ಒಳ್ಳೆ game ಹೇಳಿ.",
        "ಬೆಳಿಗ್ಗೆ ಬೇಗ ಹೇಗೆ ಏಳೋದು?",
        "English ಅಲ್ಲಿ ಧನ್ಯವಾದ ಹೇಗೆ ಹೇಳ್ತಾರೆ?",
        "ಒಂದು ಸಣ್ಣ joke ಹೇಳಿ.",
        "ಮಳೆಗಾಲದಲ್ಲಿ ಏನು ಹಾಕ್ಕೋಬೇಕು?",
        "Yoga ಮಾಡಿದ್ರೆ ಏನು ಲಾಭ?",
        "ನನಗೆ ಅಡುಗೆ ಕಲೀಬೇಕು.",
        "Train ticket ಹೇಗೆ book ಮಾಡೋದು?",
        "ನಿಮ್ಮ favourite colour ಯಾವುದು?",
        "ಓದೋದ್ರಲ್ಲಿ concentration ಹೇಗೆ ಮಾಡೋದು?",
        "ಸರಿ, ತುಂಬಾ thanks.",
    ),
    long_prompts=(
        "Taj Mahal ಬಗ್ಗೆ ಎರಡು ವಾಕ್ಯದಲ್ಲಿ ಹೇಳಿ.",
        "ಬೇಸಿಗೆಯಲ್ಲಿ health ಹೇಗೆ ನೋಡ್ಕೊಳ್ಳೋದು ಅಂತ ಎರಡು ವಾಕ್ಯದಲ್ಲಿ ಹೇಳಿ.",
        "ನಮ್ಮ ದೇಶದ ಯಾವುದಾದ್ರೂ ಒಂದು ಹಬ್ಬದ ಬಗ್ಗೆ ಎರಡು ವಾಕ್ಯದಲ್ಲಿ ಹೇಳಿ.",
        "ಒಳ್ಳೆ ನಿದ್ದೆಗೆ ಎರಡು tips ಕೊಡಿ.",
        "Bangalore city ಬಗ್ಗೆ ಎರಡು ವಾಕ್ಯ ಹೇಳಿ.",
    ),
    interruptions=(
        "ಒಂದ್ನಿಮಿಷ, ನನ್ನ ಮಾತು ಕೇಳಿ.",
        "ಸ್ವಲ್ಪ ನಿಲ್ಲಿ, ಬೇರೆ ಏನೋ ಕೇಳಬೇಕು.",
        "ಬೇಡ ಬೇಡ, ಸ್ವಲ್ಪ ನಿಲ್ಲಿ.",
        "ಕೇಳಿ, ಮಧ್ಯದಲ್ಲಿ ಒಂದು question ಇದೆ.",
        "ನಿಲ್ಲಿ ನಿಲ್ಲಿ, ನಾನು ಒಂದು ವಿಷಯ ಹೇಳಬೇಕು.",
    ),
    backchannels=("ಹೂಂ", "ಸರಿ", "ಹೌದು", "ಆಯ್ತು", "ಹೂಂ"),
    fillers=("ಅಂ", "ಉಮ್ಮ್", "ಅಂ", "ಉಮ್ಮ್", "ಅಂ"),
    paused=(
        ("ನನಗೆ train ticket", "book ಮಾಡಬೇಕು, ಹೇಗೆ ಮಾಡೋದು?"),
        ("ನನ್ನ phone", "ತುಂಬಾ slow ಆಗಿದೆ."),
        ("Bangalore ಇಂದ Mysore", "ಎಷ್ಟು ದೂರ?"),
        ("ನನಗೆ ರಾತ್ರಿ", "ನಿದ್ದೆ ಬರ್ತಿಲ್ಲ, ಏನು ಮಾಡ್ಲಿ?"),
        ("ಬೆಳಿಗ್ಗೆ ಬೇಗ", "ಹೇಗೆ ಏಳೋದು?"),
    ),
)

MARATHI = Script(
    turns=(
        "नमस्कार, तुम्ही कसे आहात?",
        "आज weather कसं असेल?",
        "सोप्या पद्धतीने चहा कसा करायचा ते सांगा.",
        "पुण्याहून मुंबई किती लांब आहे?",
        "एखाद्या चांगल्या पुस्तकाचं नाव सांगा.",
        "मला रात्री झोप येत नाही, काय करू?",
        "दिवसाला किती पाणी प्यायला हवं?",
        "Cricket बद्दल काहीतरी सांगा.",
        "माझा phone खूप slow झालाय.",
        "मुलांसाठी एखादा चांगला game सांगा.",
        "सकाळी लवकर कसं उठायचं?",
        "English मध्ये धन्यवाद कसं म्हणतात?",
        "एक छोटासा joke सांगा.",
        "पावसाळ्यात काय घालायला हवं?",
        "Yoga केल्याने काय फायदे होतात?",
        "मला स्वयंपाक शिकायचा आहे.",
        "Train ticket कसं book करायचं?",
        "तुमचा favourite colour कोणता?",
        "अभ्यासात मन कसं लावायचं?",
        "ठीक आहे, खूप खूप thanks.",
    ),
    long_prompts=(
        "ताजमहालबद्दल दोन वाक्यांत सांगा.",
        "उन्हाळ्यात तब्येतीची काळजी कशी घ्यायची, दोन वाक्यांत सांगा.",
        "आपल्या देशातल्या एखाद्या सणाबद्दल दोन वाक्यांत सांगा.",
        "चांगल्या झोपेसाठी दोन tips द्या.",
        "पुणे शहराबद्दल दोन वाक्यं सांगा.",
    ),
    interruptions=(
        "थांबा, माझं ऐका.",
        "एक मिनिट थांबा, मला अजून काहीतरी विचारायचंय.",
        "नाही नाही, जरा थांबा.",
        "ऐका, मध्येच एक प्रश्न आहे.",
        "थांब थांब, मला काहीतरी सांगायचंय.",
    ),
    backchannels=("हो", "हं", "बरं", "ठीक आहे", "हम्म"),
    fillers=("अं", "उम्म", "अं", "उम्म", "अं"),
    paused=(
        ("मला train ticket", "book करायचंय, कसं करू?"),
        ("माझा phone", "खूप slow चालतोय."),
        ("पुण्याहून मुंबई", "किती लांब आहे?"),
        ("मला रात्री", "झोप येत नाही, काय करू?"),
        ("सकाळी लवकर", "कसं उठायचं?"),
    ),
)

SCRIPTS: dict[str, Script] = {
    "hi": HINDI,
    "en-IN": ENGLISH,
    "te-IN": TELUGU,
    "kn-IN": KANNADA,
    "mr-IN": MARATHI,
}

LANGUAGES: tuple[str, ...] = tuple(SCRIPTS)


@dataclass(frozen=True, slots=True)
class Caller:
    language: str
    script: Script
    speaker: str


def script_for(language: str) -> Script:
    if language not in SCRIPTS:
        raise ValueError(f"no caller script for {language}; one of {', '.join(LANGUAGES)}")
    return SCRIPTS[language]


def caller_for(language: str, speaker: str | None = None) -> Caller:
    return Caller(language, script_for(language), speaker or caller_voice(language))
