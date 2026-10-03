using System.IO;
using UnityEngine;

public class SaveSystem : MonoBehaviour
{
    string SlotPath => Path.Combine(Application.persistentDataPath, "slot1.sav");

    public void SaveVolume(int v) { PlayerPrefs.SetInt("volume", v); }
}
